"""DOT-NL station-identity registry + id-churn detection.

The bounded, self-updating backbone for the time-series data-quality checks
(verdwenen stations / id-stabiliteit). Instead of storing a full national
snapshot every run (unbounded, ~1.2GB/day -- the thing this project already
rejected for occupancy), we keep only each station's identity fingerprint and
an append-only log of id-change events.

  dotnl_station_registry  - one row per station id ever seen:
      ndw_id, party_id, cpo_id, operator_name, lon, lat,
      first_seen, last_seen, status ('active'|'gone'), gone_date, gone_type
  dotnl_idchurn_events    - append-only: every detected id-change:
      detected_at, event_date, old_id, new_id, party_id, cpo_id,
      operator_name, event_type ('rekeyed_same'|'rekeyed_other'|'removed'),
      distance_m

Designed to piggyback on the existing 15-min occupancy cron (same fetched
features, no extra crawl). upsert_registry runs every run (cheap); detect_churn
runs at most once/day. Works against Neon in CI (DATABASE_URL) or local
Postgres, same as the other snapshot scripts.
"""

import os
import re
from datetime import datetime, timezone

import psycopg

REKEY_RADIUS_M = 30
DEFAULT_GRACE_HOURS = 24  # a station must be absent this long before it counts as gone

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS dotnl_station_registry (
    ndw_id TEXT PRIMARY KEY,
    party_id TEXT,
    cpo_id TEXT,
    operator_name TEXT,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    gone_date DATE,
    gone_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_registry_status ON dotnl_station_registry (status);
CREATE INDEX IF NOT EXISTS idx_registry_lastseen ON dotnl_station_registry (last_seen);

CREATE TABLE IF NOT EXISTS dotnl_idchurn_events (
    event_id BIGSERIAL PRIMARY KEY,
    detected_at TIMESTAMPTZ NOT NULL,
    event_date DATE NOT NULL,
    old_id TEXT NOT NULL,
    new_id TEXT,
    party_id TEXT,
    cpo_id TEXT,
    operator_name TEXT,
    event_type TEXT NOT NULL,
    distance_m DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_events_date ON dotnl_idchurn_events (event_date);
CREATE INDEX IF NOT EXISTS idx_events_party ON dotnl_idchurn_events (party_id);

CREATE TABLE IF NOT EXISTS dotnl_registry_meta (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_detect_date DATE
);
"""

UPSERT_SQL = """
INSERT INTO dotnl_station_registry
    (ndw_id, party_id, cpo_id, operator_name, lon, lat, first_seen, last_seen, status)
SELECT * FROM unnest(
    %(ids)s::text[], %(parties)s::text[], %(cpos)s::text[], %(ops)s::text[],
    %(lons)s::double precision[], %(lats)s::double precision[],
    %(now_arr)s::timestamptz[], %(now_arr)s::timestamptz[], %(active)s::text[]
)
ON CONFLICT (ndw_id) DO UPDATE SET
    last_seen = EXCLUDED.last_seen,
    operator_name = EXCLUDED.operator_name,
    party_id = EXCLUDED.party_id,
    cpo_id = EXCLUDED.cpo_id,
    lon = EXCLUDED.lon,
    lat = EXCLUDED.lat,
    status = 'active',           -- a reappearing id is active again
    gone_date = NULL,
    gone_type = NULL;
"""


def get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def party_of(fid):
    # DOT-NL ids appear in two separator schemes -- legacy colon (NL:GFX:uuid)
    # and current hyphen (NL-GFX-uuid); handle both so a station that only
    # changed id-format still attributes to one party.
    m = re.match(r"^([A-Za-z]{2})[-*:]([A-Za-z0-9]{2,4})[-*:]", fid or "")
    return f"{m.group(1).upper()}-{m.group(2).upper()}" if m else None


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_SQL)
    conn.commit()


def upsert_registry(conn, features, now, first_seen_override=None):
    """Record presence of every current station. first_seen_override lets the
    seeder backdate historical stations (e.g. the ~2026-06-04 city caches)."""
    ids, parties, cpos, ops, lons, lats = [], [], [], [], [], []
    for f in features:
        fid = f.get("id")
        if not fid:
            continue
        p = f.get("properties", {})
        coords = f.get("geometry", {}).get("coordinates") or [None, None]
        ids.append(fid)
        parties.append(party_of(fid))
        cpos.append(p.get("cpo_id"))
        ops.append(p.get("operator_name"))
        lons.append(coords[0])
        lats.append(coords[1])
    if not ids:
        return 0
    seen = first_seen_override or now
    params = {
        "ids": ids, "parties": parties, "cpos": cpos, "ops": ops,
        "lons": lons, "lats": lats, "active": ["active"] * len(ids),
        "now_arr": [now] * len(ids),
    }
    # When backdating, we want first_seen = override but last_seen = now for
    # still-present ids. The UPSERT sets both to now_arr; for the seed path we
    # pass first_seen separately below.
    if first_seen_override:
        params["now_arr"] = [seen] * len(ids)
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, params)
    conn.commit()
    return len(ids)


def detect_churn(conn, now, grace_hours=DEFAULT_GRACE_HOURS):
    """Find stations absent > grace_hours, classify each as re-keying vs
    removal by looking for a current active station at the same spot, mark them
    gone and log an event. Returns counts. Run at most once/day."""
    cutoff = now
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ndw_id, party_id, cpo_id, operator_name, lon, lat "
            "FROM dotnl_station_registry "
            "WHERE status='active' AND last_seen < %s - make_interval(hours => %s)",
            (cutoff, grace_hours),
        )
        vanished = cur.fetchall()

        counts = {"rekeyed_same": 0, "rekeyed_other": 0, "removed": 0}
        for ndw_id, party, cpo, op, lon, lat in vanished:
            new_id = new_op = None
            dist = None
            kind = "removed"
            if lon is not None and lat is not None:
                cur.execute(
                    "SELECT ndw_id, operator_name, "
                    "ST_Distance(ST_SetSRID(ST_MakePoint(lon,lat),4326)::geography, "
                    "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) AS m "
                    "FROM dotnl_station_registry "
                    "WHERE status='active' AND ndw_id <> %s "
                    "AND ST_DWithin(ST_SetSRID(ST_MakePoint(lon,lat),4326)::geography, "
                    "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s) "
                    "ORDER BY m LIMIT 1",
                    (lon, lat, ndw_id, lon, lat, REKEY_RADIUS_M),
                )
                near = cur.fetchone()
                if near:
                    new_id, new_op, dist = near
                    kind = "rekeyed_same" if (new_op or "") == (op or "") else "rekeyed_other"
            counts[kind] += 1
            cur.execute(
                "UPDATE dotnl_station_registry SET status='gone', gone_date=%s, gone_type=%s "
                "WHERE ndw_id=%s",
                (now.date(), kind, ndw_id),
            )
            cur.execute(
                "INSERT INTO dotnl_idchurn_events "
                "(detected_at, event_date, old_id, new_id, party_id, cpo_id, operator_name, event_type, distance_m)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (now, now.date(), ndw_id, new_id, party, cpo, op, kind, dist),
            )
    conn.commit()
    return counts


def maybe_detect_daily(conn, now, grace_hours=DEFAULT_GRACE_HOURS):
    """Run churn detection at most once per UTC calendar day, so the 15-min cron
    can call this every run cheaply. Returns counts if it ran, else None."""
    with conn.cursor() as cur:
        cur.execute("SELECT last_detect_date FROM dotnl_registry_meta WHERE id=1")
        row = cur.fetchone()
        last = row[0] if row else None
    if last == now.date():
        return None
    counts = detect_churn(conn, now, grace_hours)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO dotnl_registry_meta (id, last_detect_date) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET last_detect_date = EXCLUDED.last_detect_date",
            (now.date(),),
        )
    conn.commit()
    return counts


if __name__ == "__main__":
    # Standalone: just ensure the tables exist.
    conn = get_connection()
    ensure_tables(conn)
    conn.close()
    print("Registry tables ready.")

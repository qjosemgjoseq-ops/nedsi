"""Take one DOT-NL occupancy reading and fold it into dotnl_occupancy_hourly_avg
as a running average per station per (day_of_week, hour_of_day) bucket --
NOT an ever-growing raw log. A raw per-run log measured ~13MB/run (~1.2GB/day
at a 15-min interval), which blows through any free-tier database within
days; this table is instead capped at station_count x 7 x 24 rows forever,
which is what a hour-of-week forecast model actually needs anyway.

Reuses the tile-crawling fetch logic from ingest_ndw_dotnl.py (same NDW API,
same NL bbox/tiling/country-filter handling) instead of duplicating it.
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from ingest_ndw_dotnl import NL_BBOX, INITIAL_TILE_SIZE, collect_features, tile_grid

load_dotenv()

NL_TZ = ZoneInfo("Europe/Amsterdam")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dotnl_occupancy_hourly_avg (
    ndw_id TEXT NOT NULL,
    day_of_week SMALLINT NOT NULL,  -- 0=Monday .. 6=Sunday, Europe/Amsterdam local time
    hour_of_day SMALLINT NOT NULL,  -- 0-23, Europe/Amsterdam local time
    sample_count INT NOT NULL DEFAULT 0,
    avg_occupancy_pct DOUBLE PRECISION,
    total_connectors INT,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    last_updated TIMESTAMPTZ,
    PRIMARY KEY (ndw_id, day_of_week, hour_of_day)
);
"""

# Incremental running-average upsert (Welford-style): each new reading nudges
# the stored average toward itself by 1/(n+1), so the table never needs the
# raw history to keep the mean accurate. total_connectors/lon/lat aren't
# averaged -- a station's own latest reading is representative enough for
# those, so we just overwrite them.
UPSERT_SQL = """
INSERT INTO dotnl_occupancy_hourly_avg
    (ndw_id, day_of_week, hour_of_day, sample_count, avg_occupancy_pct, total_connectors, lon, lat, last_updated)
SELECT * FROM unnest(
    %(ndw_ids)s::text[], %(dows)s::smallint[], %(hods)s::smallint[], %(ones)s::int[],
    %(occs)s::double precision[], %(totals)s::int[], %(lons)s::double precision[],
    %(lats)s::double precision[], %(times)s::timestamptz[]
)
ON CONFLICT (ndw_id, day_of_week, hour_of_day) DO UPDATE SET
    sample_count = dotnl_occupancy_hourly_avg.sample_count + 1,
    avg_occupancy_pct = CASE
        WHEN EXCLUDED.avg_occupancy_pct IS NULL THEN dotnl_occupancy_hourly_avg.avg_occupancy_pct
        WHEN dotnl_occupancy_hourly_avg.avg_occupancy_pct IS NULL THEN EXCLUDED.avg_occupancy_pct
        ELSE dotnl_occupancy_hourly_avg.avg_occupancy_pct
            + (EXCLUDED.avg_occupancy_pct - dotnl_occupancy_hourly_avg.avg_occupancy_pct)
              / (dotnl_occupancy_hourly_avg.sample_count + 1)
    END,
    total_connectors = EXCLUDED.total_connectors,
    lon = EXCLUDED.lon,
    lat = EXCLUDED.lat,
    last_updated = EXCLUDED.last_updated;
"""


def get_connection():
    # In GitHub Actions (or any cloud runner) DATABASE_URL points at the free
    # Neon Postgres instance; locally it falls back to the Docker Postgres
    # via the individual POSTGRES_* vars, same as the other scripts.
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def feature_to_bucket_row(feat, captured_at_utc):
    props = feat.get("properties", {})
    availabilities = props.get("availabilities") or []
    total = sum(a.get("total") or 0 for a in availabilities)
    available = sum(a.get("available") or 0 for a in availabilities)
    occupancy_pct = round((1 - available / total) * 100, 1) if total > 0 else None
    lon, lat = feat["geometry"]["coordinates"][:2]

    local = captured_at_utc.astimezone(NL_TZ)
    day_of_week = local.weekday()  # 0=Monday .. 6=Sunday
    hour_of_day = local.hour

    return (feat.get("id"), day_of_week, hour_of_day, occupancy_pct, total, lon, lat)


def take_snapshot():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()

    seen_ids = set()
    all_features = []
    for bbox in tile_grid(NL_BBOX, INITIAL_TILE_SIZE):
        collect_features(bbox, INITIAL_TILE_SIZE, seen_ids, all_features)

    nl_features = [f for f in all_features if f["properties"].get("country") == "NLD"]
    captured_at = datetime.now(timezone.utc)
    rows = [feature_to_bucket_row(f, captured_at) for f in nl_features]

    with conn.cursor() as cur:
        cur.execute(
            UPSERT_SQL,
            {
                "ndw_ids": [r[0] for r in rows],
                "dows": [r[1] for r in rows],
                "hods": [r[2] for r in rows],
                "ones": [1] * len(rows),
                "occs": [r[3] for r in rows],
                "totals": [r[4] for r in rows],
                "lons": [r[5] for r in rows],
                "lats": [r[6] for r in rows],
                "times": [captured_at] * len(rows),
            },
        )
    conn.commit()
    conn.close()

    known_occupancy = [r[3] for r in rows if r[3] is not None]
    avg_occupancy = round(sum(known_occupancy) / len(known_occupancy), 1) if known_occupancy else None
    print(
        f"[{captured_at.isoformat()}] Snapshot: {len(rows)} stations, "
        f"avg occupancy {avg_occupancy}% ({len(rows) - len(known_occupancy)} with 0 connectors reported)",
        flush=True,
    )
    return len(rows)


if __name__ == "__main__":
    take_snapshot()

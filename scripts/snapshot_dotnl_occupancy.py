"""Take one time-series snapshot of DOT-NL station occupancy and append it to
dotnl_occupancy_snapshots (never dropped/truncated -- this accumulates real
occupancy history over time for training the forecast model, replacing the
dashboard's current simulated peak-occupancy model).

Reuses the tile-crawling fetch logic from ingest_ndw_dotnl.py (same NDW API,
same NL bbox/tiling/country-filter handling) instead of duplicating it --
this script only differs in what it does with the features: append a
timestamped occupancy row per station instead of replacing raw_ndw_dotnl.
"""

import os
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv
from ingest_ndw_dotnl import NL_BBOX, INITIAL_TILE_SIZE, collect_features, tile_grid

load_dotenv()

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dotnl_occupancy_snapshots (
    id SERIAL PRIMARY KEY,
    ndw_id TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    total_connectors INT NOT NULL,
    available_connectors INT NOT NULL,
    occupancy_pct DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_dotnl_occupancy_ndw_id_time
    ON dotnl_occupancy_snapshots (ndw_id, captured_at);
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


def feature_to_row(feat, captured_at):
    props = feat.get("properties", {})
    availabilities = props.get("availabilities") or []
    total = sum(a.get("total") or 0 for a in availabilities)
    available = sum(a.get("available") or 0 for a in availabilities)
    occupancy_pct = round((1 - available / total) * 100, 1) if total > 0 else None
    lon, lat = feat["geometry"]["coordinates"][:2]
    return (feat.get("id"), captured_at, total, available, occupancy_pct, lon, lat)


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
    rows = [feature_to_row(f, captured_at) for f in nl_features]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO dotnl_occupancy_snapshots
                (ndw_id, captured_at, total_connectors, available_connectors, occupancy_pct, lon, lat)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    conn.commit()
    conn.close()

    known_occupancy = [r[4] for r in rows if r[4] is not None]
    avg_occupancy = round(sum(known_occupancy) / len(known_occupancy), 1) if known_occupancy else None
    print(
        f"[{captured_at.isoformat()}] Snapshot: {len(rows)} stations, "
        f"avg occupancy {avg_occupancy}% ({len(rows) - len(known_occupancy)} with 0 connectors reported)",
        flush=True,
    )
    return len(rows)


if __name__ == "__main__":
    take_snapshot()

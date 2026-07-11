"""One-time migration: replace the ever-growing raw dotnl_occupancy_snapshots
log (measured ~13MB per 15-min run, ~1.2GB/day, unsustainable on any free-tier
database) with a fixed-size dotnl_occupancy_hourly_avg table -- a running
average per station per (day_of_week, hour_of_day) bucket. Capped at
stations x 7 x 24 rows regardless of how long the scheduler runs.

Backfills from whatever raw snapshot history already exists (so the hours
already collected aren't wasted), then drops the raw table to reclaim space.
Safe to run again: DROP/CREATE is idempotent, backfill only runs if the raw
table still exists.

Run locally against the Docker Postgres (default) or against Neon by setting
DATABASE_URL before running.
"""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

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

BACKFILL_SQL = """
INSERT INTO dotnl_occupancy_hourly_avg
    (ndw_id, day_of_week, hour_of_day, sample_count, avg_occupancy_pct, total_connectors, lon, lat, last_updated)
SELECT
    ndw_id,
    EXTRACT(ISODOW FROM captured_at AT TIME ZONE 'Europe/Amsterdam')::smallint - 1 AS day_of_week,
    EXTRACT(HOUR FROM captured_at AT TIME ZONE 'Europe/Amsterdam')::smallint AS hour_of_day,
    COUNT(*) AS sample_count,
    AVG(occupancy_pct) AS avg_occupancy_pct,
    (ARRAY_AGG(total_connectors ORDER BY captured_at DESC))[1] AS total_connectors,
    (ARRAY_AGG(lon ORDER BY captured_at DESC))[1] AS lon,
    (ARRAY_AGG(lat ORDER BY captured_at DESC))[1] AS lat,
    MAX(captured_at) AS last_updated
FROM dotnl_occupancy_snapshots
GROUP BY ndw_id, day_of_week, hour_of_day
ON CONFLICT (ndw_id, day_of_week, hour_of_day) DO NOTHING;
"""


def get_connection():
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


def main():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        conn.commit()

        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dotnl_occupancy_snapshots');"
        )
        raw_table_exists = cur.fetchone()[0]

        if raw_table_exists:
            cur.execute("SELECT COUNT(*) FROM dotnl_occupancy_snapshots;")
            raw_rows = cur.fetchone()[0]
            print(f"Backfilling from {raw_rows} raw rows...")
            cur.execute(BACKFILL_SQL)
            conn.commit()

            cur.execute("SELECT COUNT(*) FROM dotnl_occupancy_hourly_avg;")
            print(f"dotnl_occupancy_hourly_avg now has {cur.fetchone()[0]} rows (fixed-size going forward).")

            cur.execute("DROP TABLE dotnl_occupancy_snapshots;")
            conn.commit()
            print("Dropped the raw dotnl_occupancy_snapshots table -- storage reclaimed.")
        else:
            print("No raw dotnl_occupancy_snapshots table found -- nothing to backfill, starting fresh.")

    conn.close()


if __name__ == "__main__":
    main()

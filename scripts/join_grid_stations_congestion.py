"""Join grid_stations (real Liander/Stedin substation locations) to their
real congestion status from congestion_areas (afname direction), via
ST_Contains against the actual capaciteitskaart voedingsgebied polygon each
station physically sits in.

This is what turns "here is a substation" into "here is the substation, and
here is whether it currently has spare transport capacity" -- the actual
value proposition of plotting substations at all. Computed once here and
stored as static columns (not a live per-request join) because
congestion_areas polygons are high-resolution (927 polygons, ~16.8M vertices
total -- see ISSUES.md 2026-07-10 entry on why station_congestion is
materialized rather than a live view).

Safe to re-run: columns are added with IF NOT EXISTS, values overwritten.
"""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

ADD_COLUMNS_SQL = """
ALTER TABLE grid_stations
    ADD COLUMN IF NOT EXISTS afname_status_code SMALLINT,
    ADD COLUMN IF NOT EXISTS afname_status TEXT,
    ADD COLUMN IF NOT EXISTS afname_area TEXT,
    ADD COLUMN IF NOT EXISTS congestion_operator TEXT;
"""

JOIN_SQL = """
UPDATE grid_stations gs
SET afname_status_code = ca.status_code,
    afname_status = ca.status_label,
    afname_area = ca.area_name,
    congestion_operator = ca.operator
FROM congestion_areas ca
WHERE ca.source = 'capaciteitskaart'
  AND ca.direction = 'afname'
  AND ca.area_name IS DISTINCT FROM '0'
  AND ST_Contains(ca.geom, gs.geom);
"""


def get_connection():
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
        cur.execute(ADD_COLUMNS_SQL)
        conn.commit()
        print("Columns ready.")

        cur.execute(JOIN_SQL)
        matched = cur.rowcount
        conn.commit()
        print(f"Matched {matched} grid_stations rows to a congestion polygon.")

        cur.execute("SELECT COUNT(*) FROM grid_stations;")
        total = cur.fetchone()[0]
        print(f"Total grid_stations: {total} ({matched}/{total} = {matched/total*100:.1f}% matched)")

        cur.execute(
            """
            SELECT afname_status_code, afname_status, COUNT(*)
            FROM grid_stations
            GROUP BY 1, 2
            ORDER BY 1 NULLS LAST;
            """
        )
        print("Breakdown by status:")
        for row in cur.fetchall():
            print(" ", row)

    conn.close()


if __name__ == "__main__":
    main()

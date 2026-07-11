"""Build the gemeenten reference table: real bbox + centroid per Dutch
municipality, aggregated from the wegvakken table's own gme_naam field (NWB
data), not a hardcoded/curated list.

The Netherlands has exactly 342 gemeenten (2024-2026) -- "stad" isn't an
administrative unit at all (stadsrechten were abolished), so this replaces
the webapp's earlier 4-city hardcoded list with the real thing.
"""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS gemeenten;
CREATE TABLE gemeenten (
    naam TEXT PRIMARY KEY,
    min_lon DOUBLE PRECISION,
    min_lat DOUBLE PRECISION,
    max_lon DOUBLE PRECISION,
    max_lat DOUBLE PRECISION,
    center_lon DOUBLE PRECISION,
    center_lat DOUBLE PRECISION,
    wegvak_count INT
);
"""

BUILD_SQL = """
INSERT INTO gemeenten (naam, min_lon, min_lat, max_lon, max_lat, center_lon, center_lat, wegvak_count)
SELECT
    gme_naam,
    ST_XMin(t.bbox_wgs84), ST_YMin(t.bbox_wgs84), ST_XMax(t.bbox_wgs84), ST_YMax(t.bbox_wgs84),
    ST_X(ST_Centroid(t.bbox_wgs84)), ST_Y(ST_Centroid(t.bbox_wgs84)),
    t.wegvak_count
FROM (
    SELECT
        gme_naam,
        ST_Transform(ST_SetSRID(ST_Extent(geom)::geometry, 28992), 4326) AS bbox_wgs84,
        COUNT(*) AS wegvak_count
    FROM wegvakken
    WHERE gme_naam IS NOT NULL
    GROUP BY gme_naam
) t;
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
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(BUILD_SQL)
    conn.commit()
    print("Created gemeenten table.")

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM gemeenten;")
        print(f"Total gemeenten: {cur.fetchone()[0]}")
        cur.execute("SELECT naam, min_lon, min_lat, max_lon, max_lat FROM gemeenten ORDER BY naam LIMIT 5;")
        for row in cur.fetchall():
            print(" ", row)

    conn.close()


if __name__ == "__main__":
    main()

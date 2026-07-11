"""Load real province boundary polygons from PDOK's CBS Gebiedsindelingen WFS
(gebiedsindelingen:provincie_gegeneraliseerd, EPSG:4326) into PostGIS.

This replaces bbox-rectangle province filtering (which leaks neighboring
provinces' data near irregular borders, e.g. south Amsterdam showing up
under "Utrecht") with exact ST_Contains polygon filtering.
"""

import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

GEOJSON_PATH = Path(__file__).parent / "provincie_boundaries.geojson"

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS provincie_polygons;
CREATE TABLE provincie_polygons (
    naam TEXT PRIMARY KEY,
    geom GEOMETRY(MultiPolygon, 4326)
);
CREATE INDEX idx_provincie_polygons_geom ON provincie_polygons USING GIST (geom);
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
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        geojson = json.load(f)

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        for feature in geojson["features"]:
            naam = feature["properties"]["statnaam"]
            geom = json.dumps(feature["geometry"])
            cur.execute(
                "INSERT INTO provincie_polygons (naam, geom) VALUES (%s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))",
                (naam, geom),
            )
    conn.commit()

    print(f"Loaded {len(geojson['features'])} province polygons.")
    with conn.cursor() as cur:
        cur.execute("SELECT naam, ST_Area(geom::geography) / 1e6 AS km2 FROM provincie_polygons ORDER BY naam;")
        for row in cur.fetchall():
            print(f"  {row[0]}: {row[1]:.0f} km2")

    conn.close()


if __name__ == "__main__":
    main()

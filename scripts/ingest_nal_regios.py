"""Ingest the 6 official NAL samenwerkingsregio's (Nationale Agenda
Laadinfrastructuur: Zuidwest, G4, Noord, Oost, Noordwest, Zuid) as a fourth
scope level next to gemeente/provincie/nationaal -- gemeenten and provincies
plan laadinfra in NAL-regio verband, and the RVO/NAL monitor publishes most
of its indicators at this level, so NEDSI not knowing the level at all was a
real gap (readiness report, use cases gemeenten #7 and provincies #6).

Source: the NAL Regio FeatureServer on ArcGIS Online (public, verified live
2026-07-12), fetched as GeoJSON in WGS84 directly:
https://services.arcgis.com/kE0BiyvJHb5SwQv7/arcgis/rest/services/NAL%20Regio/FeatureServer/0
"""

import json
import os

import psycopg
import requests
from dotenv import load_dotenv
from retry import with_retries

load_dotenv()

NAL_URL = "https://services.arcgis.com/kE0BiyvJHb5SwQv7/arcgis/rest/services/NAL Regio/FeatureServer/0/query"

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS nal_regios;
CREATE TABLE nal_regios (
    naam TEXT PRIMARY KEY,
    min_lon DOUBLE PRECISION,
    min_lat DOUBLE PRECISION,
    max_lon DOUBLE PRECISION,
    max_lat DOUBLE PRECISION,
    center_lon DOUBLE PRECISION,
    center_lat DOUBLE PRECISION,
    geom geometry(MultiPolygon, 4326)
);
CREATE INDEX idx_nal_regios_geom ON nal_regios USING GIST (geom);
"""


def get_connection():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


@with_retries()
def fetch_nal_regios():
    response = requests.get(
        NAL_URL,
        params={
            "where": "1=1",
            "outFields": "NAL_regio",
            "returnGeometry": "true",
            "f": "geojson",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["features"]


def main():
    features = fetch_nal_regios()
    print(f"Fetched {len(features)} NAL regio polygons.")

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        for feature in features:
            naam = feature["properties"]["NAL_regio"]
            geom = json.dumps(feature["geometry"])
            cur.execute(
                """
                INSERT INTO nal_regios (naam, min_lon, min_lat, max_lon, max_lat, center_lon, center_lat, geom)
                SELECT
                    %s,
                    ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g),
                    ST_X(ST_Centroid(g)), ST_Y(ST_Centroid(g)),
                    g
                FROM (SELECT ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)) AS g) t
                """,
                (naam, geom),
            )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT naam, ROUND(min_lon::numeric,3), ROUND(min_lat::numeric,3), ROUND(max_lon::numeric,3), ROUND(max_lat::numeric,3) FROM nal_regios ORDER BY naam;")
        for row in cur.fetchall():
            print(" ", row)

    conn.close()


if __name__ == "__main__":
    main()

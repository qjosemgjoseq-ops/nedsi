"""Ingest real Dutch grid-operator (netbeheerder) service-area boundaries,
so NEDSI can tell a user which DSO serves a given address and deep-link them
straight to that DSO's own aansluiting-aanvraag/storing/capaciteit-check
pages, instead of leaving them to figure out "who do I even call".

Source: Provincie Zuid-Holland's geoserver, itself sourced from an
Alliander-hosted ArcGIS layer (verified public, CC0/PDM, 2026-07-12):
https://geodata.zuid-holland.nl/geoserver/grenzen/wfs, layer
grenzen:NETBEHEERDERS_GRENZEN. 28 polygons covering all of NL, one row per
netbeheerder x provincie combination.
"""

import io
import json
import os
import zipfile

import psycopg
import requests
from dotenv import load_dotenv
from retry import with_retries

load_dotenv()

WFS_URL = "https://geodata.zuid-holland.nl/geoserver/grenzen/wfs"

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS netbeheerder_gebieden;
CREATE TABLE netbeheerder_gebieden (
    id SERIAL PRIMARY KEY,
    netbeheerder TEXT NOT NULL,
    provincie TEXT,
    geom geometry(MultiPolygon, 4326)
);
CREATE INDEX idx_netbeheerder_gebieden_geom ON netbeheerder_gebieden USING GIST (geom);
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
def fetch_geojson():
    response = requests.get(
        WFS_URL,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": "grenzen:NETBEHEERDERS_GRENZEN",
            "outputFormat": "application/json",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def main():
    geojson = fetch_geojson()
    features = geojson["features"]

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        for feature in features:
            props = feature["properties"]
            netbeheerder = props.get("Netbeheerder")
            provincie = props.get("Provincie")
            geom = json.dumps(feature["geometry"])
            # The WFS returns coordinates in RD New (EPSG:28992, per its own
            # crs block) even though ST_GeomFromGeoJSON doesn't read that
            # block -- tag as 28992 first, then transform, or every
            # ST_Contains lookup against real WGS84 points silently misses.
            cur.execute(
                """
                INSERT INTO netbeheerder_gebieden (netbeheerder, provincie, geom)
                VALUES (%s, %s, ST_Multi(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 28992), 4326)))
                """,
                (netbeheerder, provincie, geom),
            )
    conn.commit()

    print(f"Loaded {len(features)} netbeheerder_gebieden polygons.")
    with conn.cursor() as cur:
        cur.execute("SELECT netbeheerder, COUNT(*) FROM netbeheerder_gebieden GROUP BY 1 ORDER BY 1;")
        for row in cur.fetchall():
            print(" ", row)

    conn.close()


if __name__ == "__main__":
    main()

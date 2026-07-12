"""Enrich anonymous Zuid-Holland grid_stations rows (mostly Stedin, whose own
open data has no station names -- see ISSUES.md 2026-07-12) with real names
from Zuid-Holland province's own CC0 TenneT+netbeheerders station dataset
(184 named points, verified live 2026-07-12):
https://geoservices.zuid-holland.nl/arcgis/rest/services/Energie/Energie/MapServer/3

Matches by nearest point within a small tolerance (stations from different
sources for the same physical substation are rarely more than ~30m apart;
50m tolerance keeps false-positive matches unlikely while catching real
digitization offset).
"""

import os

import psycopg
import requests
from dotenv import load_dotenv
from retry import with_retries

load_dotenv()

ZH_STATIONS_URL = "https://geoservices.zuid-holland.nl/arcgis/rest/services/Energie/Energie/MapServer/3/query"
MATCH_TOLERANCE_METERS = 50


def get_connection():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


@with_retries()
def fetch_zh_stations():
    response = requests.get(
        ZH_STATIONS_URL,
        params={
            "where": "1=1",
            "outFields": "naam_station,station_beheerder,type_station",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["features"]


def main():
    features = fetch_zh_stations()
    print(f"Fetched {len(features)} named ZH stations.")

    conn = get_connection()
    matched = 0
    with conn.cursor() as cur:
        for feat in features:
            name = feat["attributes"]["naam_station"]
            beheerder = feat["attributes"]["station_beheerder"]
            geom = feat.get("geometry")
            if not name or not geom:
                continue
            lon, lat = geom["x"], geom["y"]

            # Only fill in names we don't already have -- Liander's own data
            # already has real names, this is specifically for filling the
            # gap left by Stedin's anonymous polygons (and picks up Westland
            # Infra/Tennet as a bonus where they overlap our existing rows).
            cur.execute(
                """
                UPDATE grid_stations
                SET name = %s
                WHERE name IS NULL
                  AND ST_DWithin(
                        geom::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        %s
                      )
                """,
                (name, lon, lat, MATCH_TOLERANCE_METERS),
            )
            matched += cur.rowcount

    conn.commit()
    print(f"Updated {matched} grid_stations rows with real names (within {MATCH_TOLERANCE_METERS}m).")

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM grid_stations WHERE name IS NOT NULL;")
        print(f"Total grid_stations with a real name now: {cur.fetchone()[0]}")

    conn.close()


if __name__ == "__main__":
    main()

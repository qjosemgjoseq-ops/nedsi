"""Ingest OpenStreetMap EV charging stations in the Netherlands into raw_osm_charging."""

import os
from datetime import datetime, timezone

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Jsonb
from retry import with_retries

load_dotenv()

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass rejects requests with a generic/default User-Agent.
HEADERS = {"User-Agent": "NEDSI/1.0 (EV charging data platform)"}
# [bbox:...] hard-clips every statement to mainland NL. Without it, the
# ISO3166-1=NL area also matches the Kingdom-level relation, which pulls in
# overseas territories like Aruba (see ISSUES.md).
QUERY = """
[out:json][timeout:120][bbox:50.6,3.2,53.7,7.3];
area["ISO3166-1"="NL"][admin_level=2]->.nl;
(
  node["amenity"="charging_station"](area.nl);
  way["amenity"="charging_station"](area.nl);
  relation["amenity"="charging_station"](area.nl);
);
out center;
"""

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS raw_osm_charging;
CREATE TABLE raw_osm_charging (
    id SERIAL PRIMARY KEY,
    osm_type TEXT,
    osm_id BIGINT,
    raw JSONB,
    geom geometry(Point, 4326),
    fetched_at TIMESTAMPTZ
);
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
def fetch_elements():
    response = requests.post(
        OVERPASS_URL,
        data={"data": QUERY},
        headers=HEADERS,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["elements"]


def main():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Created empty raw_osm_charging table.")

    print("Querying Overpass API for amenity=charging_station in NL...")
    elements = fetch_elements()
    fetched_at = datetime.now(timezone.utc)

    rows = []
    for el in elements:
        if el["type"] == "node":
            lat, lon = el["lat"], el["lon"]
        else:
            center = el.get("center")
            if not center:
                continue
            lat, lon = center["lat"], center["lon"]
        rows.append((el["type"], el["id"], Jsonb(el), lon, lat, fetched_at))

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO raw_osm_charging (osm_type, osm_id, raw, geom, fetched_at)
            VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
            """,
            rows,
        )
    conn.commit()

    print(f"Done. Total rows inserted: {len(rows)}")
    conn.close()


if __name__ == "__main__":
    main()

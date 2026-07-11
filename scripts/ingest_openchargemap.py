"""Ingest OpenChargeMap charging station data for the Netherlands into raw_openchargemap."""

import os
from datetime import datetime, timezone

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Jsonb
from retry import with_retries

load_dotenv()

API_URL = "https://api.openchargemap.io/v3/poi/"
HEADERS = {"User-Agent": "NEDSI/1.0 (EV charging data platform)"}
PAGE_SIZE = 5000

# The API ignores `offset`, so we paginate with sortby=id_asc + greaterthanid
# instead; the UNIQUE constraint on ocm_id guards against duplicate pages.
CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS raw_openchargemap;
CREATE TABLE raw_openchargemap (
    id SERIAL PRIMARY KEY,
    ocm_id BIGINT UNIQUE,
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
def fetch_page(api_key, greater_than_id):
    response = requests.get(
        API_URL,
        params={
            "output": "json",
            "countrycode": "NL",
            "maxresults": PAGE_SIZE,
            "sortby": "id_asc",
            "greaterthanid": greater_than_id,
            "compact": "true",
            "verbose": "false",
        },
        headers={**HEADERS, "X-API-Key": api_key},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def main():
    api_key = os.environ.get("OCM_API_KEY")
    if not api_key:
        raise SystemExit(
            "OCM_API_KEY is not set in .env — get a free key at "
            "https://openchargemap.org/site/develop/api and add it there."
        )

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Created empty raw_openchargemap table.")

    last_id = 0
    total = 0
    fetched_at = datetime.now(timezone.utc)

    while True:
        page = fetch_page(api_key, last_id)
        if not page:
            break

        rows = []
        for poi in page:
            addr = poi.get("AddressInfo") or {}
            lon, lat = addr.get("Longitude"), addr.get("Latitude")
            if lon is None or lat is None:
                continue
            rows.append((poi.get("ID"), Jsonb(poi), lon, lat, fetched_at))

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO raw_openchargemap (ocm_id, raw, geom, fetched_at)
                VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
                ON CONFLICT (ocm_id) DO NOTHING
                """,
                rows,
            )
        conn.commit()

        total += len(rows)
        last_id = max(poi["ID"] for poi in page)
        print(f"  fetched {len(page)} (up to id {last_id}), total inserted so far: {total}")

        if len(page) < PAGE_SIZE:
            break

    print(f"Done. Total rows inserted: {total}")
    conn.close()


if __name__ == "__main__":
    main()

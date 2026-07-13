"""Create the postcode->coordinate geocode cache table.

Backs the live storingen (energieonderbrekingen.nl) feature in the webapp:
that API returns postcode + city per disruption but no coordinates
(location.features.geometry.coordinates is always [0,0]). We resolve
postcode -> lat/lon ourselves via the PDOK Locatieserver (same source
PostcodeSearch already uses) and cache the result here so repeat storingen
for the same postcode (common during a multi-hour outage) don't re-hit PDOK.
"""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS postcode_geocode_cache (
    postcode TEXT PRIMARY KEY,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    plaats TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def main():
    with psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
    print("postcode_geocode_cache ready.")


if __name__ == "__main__":
    main()

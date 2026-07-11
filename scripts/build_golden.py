"""Fuse the three raw source tables into the golden_stations table.

Coordinates come from OCM/OSM (trusted); NDW coordinates can be off by tens
of meters, so NDW is only trusted for live status. See fusion.py for the
matching rules.
"""

import os
import sys
from datetime import datetime, timezone

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fusion import fuse  # noqa: E402
from retry import with_retries  # noqa: E402

load_dotenv()

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS golden_stations;
CREATE TABLE golden_stations (
    id SERIAL PRIMARY KEY,
    geom geometry(Point, 4326),
    coord_source TEXT,
    operator TEXT,
    ocm_ids BIGINT[],
    osm_ids BIGINT[],
    ndw_ids TEXT[],
    n_sources INT,
    confidence REAL,
    match_method TEXT,
    ndw_status JSONB,
    created_at TIMESTAMPTZ
);
CREATE INDEX golden_stations_geom_idx ON golden_stations USING GIST (geom);
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
def fetch_ocm_operator_names():
    """The OCM data was fetched compact (operator IDs only); map IDs to names."""
    response = requests.get(
        "https://api.openchargemap.io/v3/referencedata/",
        headers={
            "User-Agent": "NEDSI/1.0 (EV charging data platform)",
            "X-API-Key": os.environ["OCM_API_KEY"],
        },
        timeout=60,
    )
    response.raise_for_status()
    return {op["ID"]: op["Title"] for op in response.json()["Operators"]}


def parse_int(value):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def load_records(conn):
    records = []
    operator_names = fetch_ocm_operator_names()
    print(f"Fetched {len(operator_names)} OCM operator names.")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ocm_id, raw->>'OperatorID', raw->>'NumberOfPoints',
                   ST_X(geom), ST_Y(geom)
            FROM raw_openchargemap
            """
        )
        for ocm_id, op_id, n_points, lon, lat in cur.fetchall():
            records.append(
                {
                    "source": "ocm",
                    "source_id": ocm_id,
                    "lon": lon,
                    "lat": lat,
                    "operator": operator_names.get(parse_int(op_id)),
                    "connector_count": parse_int(n_points),
                }
            )

        cur.execute(
            """
            SELECT osm_id, raw->'tags'->>'operator', raw->'tags'->>'capacity',
                   ST_X(geom), ST_Y(geom)
            FROM raw_osm_charging
            """
        )
        for osm_id, operator, capacity, lon, lat in cur.fetchall():
            records.append(
                {
                    "source": "osm",
                    "source_id": osm_id,
                    "lon": lon,
                    "lat": lat,
                    "operator": operator,
                    "connector_count": parse_int(capacity),
                }
            )

        cur.execute(
            """
            SELECT ndw_id, raw->'properties'->>'operator_name',
                   (SELECT SUM((a->>'total')::int)
                    FROM jsonb_array_elements(raw->'properties'->'availabilities') a),
                   ST_X(geom), ST_Y(geom)
            FROM raw_ndw_dotnl
            """
        )
        for ndw_id, operator, total, lon, lat in cur.fetchall():
            records.append(
                {
                    "source": "ndw",
                    "source_id": ndw_id,
                    "lon": lon,
                    "lat": lat,
                    "operator": operator,
                    "connector_count": int(total) if total is not None else None,
                }
            )

    return records


def load_ndw_status(conn, ndw_ids):
    """Live availability snapshots for the NDW members of golden records."""
    if not ndw_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ndw_id, raw->'properties'->'availabilities', raw->'properties'->>'last_updated'
            FROM raw_ndw_dotnl WHERE ndw_id = ANY(%s)
            """,
            (list(ndw_ids),),
        )
        return {
            row[0]: {"availabilities": row[1], "last_updated": row[2]}
            for row in cur.fetchall()
        }


def main():
    conn = get_connection()

    records = load_records(conn)
    counts = {}
    for r in records:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    print(f"Loaded {len(records)} records: {counts}")

    print("Fusing...")
    golden = fuse(records)
    print(f"Fused into {len(golden)} golden records.")

    all_ndw_ids = {i for g in golden for i in g["members"].get("ndw", [])}
    ndw_status = load_ndw_status(conn, all_ndw_ids)

    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        created_at = datetime.now(timezone.utc)
        rows = []
        for g in golden:
            first_ndw = (g["members"].get("ndw") or [None])[0]
            status = ndw_status.get(first_ndw)
            rows.append(
                (
                    g["lon"],
                    g["lat"],
                    g["coord_source"],
                    g["operator"],
                    g["members"].get("ocm"),
                    g["members"].get("osm"),
                    g["members"].get("ndw"),
                    g["n_sources"],
                    g["confidence"],
                    g["match_method"],
                    Jsonb(status) if status else None,
                    created_at,
                )
            )
        cur.executemany(
            """
            INSERT INTO golden_stations
                (geom, coord_source, operator, ocm_ids, osm_ids, ndw_ids,
                 n_sources, confidence, match_method, ndw_status, created_at)
            VALUES (ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
            """,
            rows,
        )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n_sources, COUNT(*) FROM golden_stations
            GROUP BY n_sources ORDER BY n_sources
            """
        )
        for n_sources, count in cur.fetchall():
            print(f"  {n_sources} source(s): {count} golden records")

    conn.close()


if __name__ == "__main__":
    main()

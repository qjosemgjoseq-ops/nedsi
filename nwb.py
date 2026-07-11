"""Helpers for querying the NWB road segments (wegvakken) table."""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def get_nearest_wegvak(lat: float, lon: float, conn=None) -> dict | None:
    """Return the road segment (wegvak) closest to the given WGS84 lat/lon.

    Pass an existing `conn` to reuse a connection when calling this many times
    (e.g. snapping a whole table) instead of opening a new one per call.
    """
    sql = """
        SELECT
            wvk_id,
            stt_naam,
            wpsnaam,
            gme_naam,
            ST_Distance(
                geom,
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 28992)
            ) AS distance_m
        FROM wegvakken
        ORDER BY geom <-> ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 28992)
        LIMIT 1;
    """
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {"lat": lat, "lon": lon})
            row = cur.fetchone()
    finally:
        if owns_conn:
            conn.close()

    if row is None:
        return None

    wvk_id, stt_naam, wpsnaam, gme_naam, distance_m = row
    return {
        "wvk_id": wvk_id,
        "stt_naam": stt_naam,
        "wpsnaam": wpsnaam,
        "gme_naam": gme_naam,
        "distance_m": distance_m,
    }


if __name__ == "__main__":
    # Dam square, Amsterdam
    result = get_nearest_wegvak(52.373169, 4.892839)
    print(result)

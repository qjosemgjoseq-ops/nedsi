"""Ingest live charging point data from the NDW DOT-NL API into raw_ndw_dotnl.

Coordinates from this source can be off by tens of meters -- we store them
as-is here and correct that later in the fusion step.
"""

import os
import time
from datetime import datetime, timezone

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Jsonb
from retry import with_retries

load_dotenv()

API_URL = "https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/charge-point-data/v1/features"
# NDW requires a descriptive User-Agent identifying the client.
HEADERS = {"User-Agent": "NEDSI/1.0 (EV charging data platform)"}

# Netherlands bounding box (minLon, minLat, maxLon, maxLat), with a small margin.
NL_BBOX = (3.2, 50.6, 7.3, 53.7)
INITIAL_TILE_SIZE = 0.5  # degrees; 0.5x0.5 = 0.25 deg^2, under the API's 1.0 deg^2 max
MIN_TILE_SIZE = 0.02  # floor so a saturated dense area can't recurse forever
MAX_FEATURES_PER_REQUEST = 1000  # the API's documented cap per request

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS raw_ndw_dotnl;
CREATE TABLE raw_ndw_dotnl (
    id SERIAL PRIMARY KEY,
    ndw_id TEXT,
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
def fetch_tile(bbox):
    response = requests.get(
        API_URL,
        params={"bbox": ",".join(str(v) for v in bbox)},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    time.sleep(0.15)  # stay comfortably under the API's 10 requests/second limit
    return response.json()["features"]


def tile_grid(bbox, size):
    """Yield non-overlapping sub-bboxes of `size` degrees covering `bbox`."""
    min_lon, min_lat, max_lon, max_lat = bbox
    lon = min_lon
    while lon < max_lon:
        lat = min_lat
        while lat < max_lat:
            yield (lon, lat, min(lon + size, max_lon), min(lat + size, max_lat))
            lat += size
        lon += size


def collect_features(bbox, size, seen_ids, all_features):
    """Fetch a tile; if it's saturated (hit the API cap), split into 4 and recurse."""
    features = fetch_tile(bbox)

    if len(features) >= MAX_FEATURES_PER_REQUEST and size > MIN_TILE_SIZE:
        min_lon, min_lat, max_lon, max_lat = bbox
        mid_lon = (min_lon + max_lon) / 2
        mid_lat = (min_lat + max_lat) / 2
        half = size / 2
        for sub_bbox in [
            (min_lon, min_lat, mid_lon, mid_lat),
            (mid_lon, min_lat, max_lon, mid_lat),
            (min_lon, mid_lat, mid_lon, max_lat),
            (mid_lon, mid_lat, max_lon, max_lat),
        ]:
            collect_features(sub_bbox, half, seen_ids, all_features)
        return

    for feat in features:
        fid = feat.get("id")
        if fid in seen_ids:
            continue
        seen_ids.add(fid)
        all_features.append(feat)


def main():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Created empty raw_ndw_dotnl table.")

    seen_ids = set()
    all_features = []
    tiles = list(tile_grid(NL_BBOX, INITIAL_TILE_SIZE))
    print(f"Crawling {len(tiles)} base tiles over the Netherlands (splitting further where dense)...")

    for i, bbox in enumerate(tiles):
        collect_features(bbox, INITIAL_TILE_SIZE, seen_ids, all_features)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(tiles)} base tiles done, {len(all_features)} unique features so far")

    # The rectangular bbox tiles inevitably catch a sliver of Belgium/Germany
    # near the border; the API tells us each feature's country directly, so
    # filter on that instead of trying to draw a tighter bbox (see ISSUES.md).
    nl_features = [f for f in all_features if f["properties"].get("country") == "NLD"]
    skipped = len(all_features) - len(nl_features)
    if skipped:
        print(f"Dropped {skipped} non-NLD features caught by the bbox margin (border crossings).")

    fetched_at = datetime.now(timezone.utc)
    rows = []
    for feat in nl_features:
        lon, lat = feat["geometry"]["coordinates"][:2]
        rows.append((feat.get("id"), Jsonb(feat), lon, lat, fetched_at))

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO raw_ndw_dotnl (ndw_id, raw, geom, fetched_at)
            VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
            """,
            rows,
        )
    conn.commit()

    print(f"Done. Total rows inserted: {len(rows)}")
    conn.close()


if __name__ == "__main__":
    main()

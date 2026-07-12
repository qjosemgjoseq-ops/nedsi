"""Ingest real electricity grid station (substation) locations from Liander
and Stedin's public open data into grid_stations -- lets the dashboard show
not just "this area is congested" but "here is the physical station".

Liander: public WFS (dservices1.arcgis.com), GML32 output, paginated
(server caps at 3000 features/page regardless of requested count). Includes
name + voltage level per station -- CC-BY 4.0, verified live 2026-07-11.

Stedin: plain shapefile downloads (stedin.net), but the "station" features
are anonymous building-footprint POLYGONS with an always-empty ID field, no
name -- much sparser than Liander's. We use each polygon's centroid as the
station point; popups just say "Stedin" with no further detail. Verified
2026-07-11 across the whole dataset (34,569 records, 0 with a populated ID).

Enexis has no equivalent direct-download open dataset -- their station data
requires a manual request via the Partners in Energie portal (multi-day
turnaround, possible cost), so it's not included here.
"""

import io
import os
import time
import xml.etree.ElementTree as ET
import zipfile

import psycopg
import requests
import shapefile
from dotenv import load_dotenv
from retry import with_retries

load_dotenv()

LIANDER_WFS_URL = "https://dservices1.arcgis.com/v6W5HAVrpgSg3vts/arcgis/services/Liander_Open_Data_Elektra_WFS/WFSServer"
LIANDER_LAYERS = {
    "middenspanning": "Liander_Open_Data_Elektra_WFS:Middenspanningsstation",
    "hoogspanning": "Liander_Open_Data_Elektra_WFS:Hoogspanningsstation",
}
LIANDER_PAGE_CAP = 3000  # server-enforced cap per request, verified 2026-07-11
# This WFS's startIndex pagination is broken (any startIndex > 0 returns zero
# features, verified 2026-07-11) -- so we tile the Netherlands' RD New extent
# into bboxes instead, same recursive-split-when-saturated strategy already
# proven in ingest_ndw_dotnl.py.
NL_BBOX_RD = (0, 300000, 280000, 625000)  # minx, miny, maxx, maxy, EPSG:28992
INITIAL_TILE_SIZE_RD = 50000  # meters
MIN_TILE_SIZE_RD = 1000

STEDIN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

STEDIN_URLS = {
    "hoogspanning": "https://www.stedin.net/-/media/project/online/files/zakelijk/branches/liggingsdata/hoogspanningsstations.zip?rev=c653a9a41141476e9045589951a0a3d6",
    "middenspanning": "https://www.stedin.net/-/media/project/online/files/zakelijk/branches/liggingsdata/middenspanningsstations.zip?rev=4eb35c39748a48efac12b9b45b785f7a",
}
STEDIN_SHAPEFILE_NAMES = {
    "hoogspanning": "Hoogspanningsstations",
    "middenspanning": "Middenspanningsstations",
}

GML_NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "wfs": "http://www.opengis.net/wfs/2.0",
    "l": "https://dservices1.arcgis.com/v6W5HAVrpgSg3vts/arcgis/services/Liander_Open_Data_Elektra_WFS/WFSServer",
}

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS grid_stations;
CREATE TABLE grid_stations (
    id SERIAL PRIMARY KEY,
    operator TEXT NOT NULL,
    station_type TEXT NOT NULL,  -- 'hoogspanning' | 'middenspanning'
    name TEXT,
    voltage_level TEXT,
    external_id TEXT,
    geom geometry(Point, 4326)
);
CREATE INDEX idx_grid_stations_geom ON grid_stations USING GIST (geom);
"""

INSERT_SQL = """
INSERT INTO grid_stations (operator, station_type, name, voltage_level, external_id, geom)
VALUES (%s, %s, %s, %s, %s, ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 28992), 4326))
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
def fetch_liander_bbox(layer_name, bbox_rd):
    minx, miny, maxx, maxy = bbox_rd
    response = requests.get(
        LIANDER_WFS_URL,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": layer_name,
            "outputFormat": "GML32",
            "count": LIANDER_PAGE_CAP,
            "bbox": f"{minx},{miny},{maxx},{maxy},urn:ogc:def:crs:EPSG::28992",
        },
        timeout=60,
    )
    response.raise_for_status()
    time.sleep(0.15)
    return response.text


def parse_liander_gml(xml_text):
    root = ET.fromstring(xml_text)
    rows = []
    for member in root.findall("wfs:member", GML_NS):
        station_el = list(member)[0]
        pos_el = station_el.find(".//gml:pos", GML_NS)
        if pos_el is None or not pos_el.text:
            continue
        x, y = (float(v) for v in pos_el.text.split())

        def field(tag):
            el = station_el.find(f"l:{tag}", GML_NS)
            return el.text if el is not None else None

        rows.append(
            {
                "name": field("Naam"),
                "voltage_level": field("Spanningsniveaus"),
                "external_id": field("ID"),
                "x": x,
                "y": y,
            }
        )
    return rows


def tile_grid_rd(bbox, size):
    minx, miny, maxx, maxy = bbox
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            yield (x, y, min(x + size, maxx), min(y + size, maxy))
            y += size
        x += size


def collect_liander_features(layer_name, bbox, size, seen_ids, all_rows):
    """Fetch a tile; if saturated (hit the server's page cap), split into 4 and recurse."""
    xml_text = fetch_liander_bbox(layer_name, bbox)
    rows = parse_liander_gml(xml_text)

    if len(rows) >= LIANDER_PAGE_CAP and size > MIN_TILE_SIZE_RD:
        minx, miny, maxx, maxy = bbox
        mid_x = (minx + maxx) / 2
        mid_y = (miny + maxy) / 2
        half = size / 2
        for sub_bbox in [
            (minx, miny, mid_x, mid_y),
            (mid_x, miny, maxx, mid_y),
            (minx, mid_y, mid_x, maxy),
            (mid_x, mid_y, maxx, maxy),
        ]:
            collect_liander_features(layer_name, sub_bbox, half, seen_ids, all_rows)
        return

    for r in rows:
        key = r["external_id"] or (r["x"], r["y"])
        if key in seen_ids:
            continue
        seen_ids.add(key)
        all_rows.append(r)


def ingest_liander(conn):
    total = 0
    with conn.cursor() as cur:
        for station_type, layer_name in LIANDER_LAYERS.items():
            seen_ids = set()
            all_rows = []
            tiles = list(tile_grid_rd(NL_BBOX_RD, INITIAL_TILE_SIZE_RD))
            for i, bbox in enumerate(tiles):
                collect_liander_features(layer_name, bbox, INITIAL_TILE_SIZE_RD, seen_ids, all_rows)
                if (i + 1) % 10 == 0:
                    print(f"  Liander {station_type}: {i + 1}/{len(tiles)} tiles, {len(all_rows)} so far", flush=True)

            cur.executemany(
                INSERT_SQL,
                [
                    ("Liander", station_type, r["name"], r["voltage_level"], r["external_id"], r["x"], r["y"])
                    for r in all_rows
                ],
            )
            total += len(all_rows)
            print(f"  Liander {station_type}: {len(all_rows)} total", flush=True)
    conn.commit()
    return total


@with_retries()
def download_stedin_zip(url):
    response = requests.get(url, headers=STEDIN_HEADERS, timeout=60)
    response.raise_for_status()
    return response.content


def ingest_stedin(conn):
    total = 0
    with conn.cursor() as cur:
        for station_type, url in STEDIN_URLS.items():
            zip_bytes = download_stedin_zip(url)
            base_name = STEDIN_SHAPEFILE_NAMES[station_type]
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                shp = io.BytesIO(zf.read(f"{base_name}.shp"))
                dbf = io.BytesIO(zf.read(f"{base_name}.dbf"))
                shx = io.BytesIO(zf.read(f"{base_name}.shx"))
                sf = shapefile.Reader(shp=shp, dbf=dbf, shx=shx)

                rows = []
                for shape_rec in sf.iterShapeRecords():
                    points = shape_rec.shape.points
                    if not points:
                        continue
                    # Station "shapes" here are building-footprint polygons with no
                    # name/ID (verified empty across the whole dataset) -- use the
                    # simple centroid of the ring as the station's point location.
                    cx = sum(p[0] for p in points) / len(points)
                    cy = sum(p[1] for p in points) / len(points)
                    rows.append(("Stedin", station_type, None, None, None, cx, cy))

                cur.executemany(INSERT_SQL, rows)
                total += len(rows)
                print(f"  Stedin {station_type}: +{len(rows)} (total {total})", flush=True)
    conn.commit()
    return total


def main():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Created grid_stations table.")

    print("Ingesting Liander (WFS)...")
    liander_count = ingest_liander(conn)

    print("Ingesting Stedin (shapefile)...")
    stedin_count = ingest_stedin(conn)

    print(f"Done. Liander: {liander_count}, Stedin: {stedin_count}, total: {liander_count + stedin_count}")
    conn.close()


if __name__ == "__main__":
    main()

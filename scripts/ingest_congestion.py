"""Ingest Dutch grid congestion polygons into the congestion_areas table.

Two sources, both official ArcGIS FeatureServers:

1. Capaciteitskaart (Netbeheer Nederland / Partners in Energie) — one national
   layer of ~927 supply areas (voedingsgebieden) per direction. Status codes
   (from the layer's own renderer): 0 = available, 1 = limited, 2 = under
   investigation (queue), 3 = shortage (queue). We ingest both directions:
   'afname' (offtake) from the _afname service and 'invoeding' (feed-in, aka
   teruglevering/opwek) from the _teruglevering service.

2. Liander transportschaarste (the dataset registered on data.overheid.nl,
   CC-BY 4.0, published via Alliander's ArcGIS org) — municipality-level
   polygons with a simple color status. LDN = Levering (afname),
   ODN = Opwek (invoeding), per Liander's own map descriptions.
"""

import os
import sys
from datetime import datetime, timezone

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from retry import with_retries  # noqa: E402

load_dotenv()

HEADERS = {"User-Agent": "NEDSI/1.0 (EV charging data platform)"}

CAPKAART_BASE = "https://services.arcgis.com/nSZVuSZjHpEZZbRo/arcgis/rest/services"
LIANDER_BASE = "https://services1.arcgis.com/v6W5HAVrpgSg3vts/arcgis/rest/services"

CAPKAART_STATUS_LABELS = {
    0: "Transportcapaciteit beschikbaar zonder wachtrij",
    1: "Transportcapaciteit beperkt beschikbaar zonder wachtrij",
    2: "Gebied is in onderzoek met wachtrij",
    3: "Tekort aan transportcapaciteit met wachtrij",
}

LAYERS = [
    # (source, direction, service url, status extractor, page_size)
    (
        "capaciteitskaart",
        "afname",
        f"{CAPKAART_BASE}/Capaciteitskaart_elektriciteitsnet_v2_afname/FeatureServer/0",
        lambda props: (props.get("afname"), CAPKAART_STATUS_LABELS.get(props.get("afname"))),
        100,  # this layer's polygons are detailed enough that pageSize=1000 causes 504s
    ),
    (
        "capaciteitskaart",
        "invoeding",
        f"{CAPKAART_BASE}/Capaciteitskaart_elektriciteitsnet_v2_teruglevering/FeatureServer/0",
        lambda props: (props.get("opwek"), CAPKAART_STATUS_LABELS.get(props.get("opwek"))),
        100,
    ),
    (
        "liander_transportschaarste",
        "afname",
        f"{LIANDER_BASE}/Transportschaarste_LDN_(Publicatie)__(View)/FeatureServer/0",
        lambda props: (None, props.get("kleur")),
        200,
    ),
    (
        "liander_transportschaarste",
        "invoeding",
        f"{LIANDER_BASE}/Transportschaarste_ODN_(Publicatie)__(View)/FeatureServer/0",
        lambda props: (None, props.get("kleur")),
        200,
    ),
]

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS congestion_areas;
CREATE TABLE congestion_areas (
    id SERIAL PRIMARY KEY,
    source TEXT,
    direction TEXT,          -- 'afname' (offtake) or 'invoeding' (feed-in)
    status_code INT,         -- capaciteitskaart only (0..3); NULL for Liander
    status_label TEXT,
    area_name TEXT,
    operator TEXT,
    raw JSONB,
    geom geometry(MultiPolygon, 4326),
    fetched_at TIMESTAMPTZ
);
CREATE INDEX congestion_areas_geom_idx ON congestion_areas USING GIST (geom);
"""

CREATE_VIEW_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_matviews WHERE matviewname = 'station_congestion') THEN
        EXECUTE 'DROP MATERIALIZED VIEW station_congestion';
    ELSIF EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'station_congestion') THEN
        EXECUTE 'DROP VIEW station_congestion';
    END IF;
END $$;
CREATE MATERIALIZED VIEW station_congestion AS
SELECT
    g.id AS station_id,
    g.operator,
    g.n_sources,
    g.confidence,
    g.wegvak_id,
    g.geom,
    ck_afname.status_code   AS afname_status_code,
    ck_afname.status_label  AS afname_status,
    ck_afname.area_name     AS afname_area,
    ck_afname.operator      AS grid_operator,
    ck_inv.status_code      AS invoeding_status_code,
    ck_inv.status_label     AS invoeding_status,
    li_afname.status_label  AS liander_afname_status,
    li_inv.status_label     AS liander_invoeding_status
FROM golden_stations g
LEFT JOIN LATERAL (
    SELECT c.status_code, c.status_label, c.area_name, c.operator
    FROM congestion_areas c
    WHERE c.source = 'capaciteitskaart' AND c.direction = 'afname'
      AND ST_Contains(c.geom, g.geom)
    ORDER BY c.status_code DESC NULLS LAST LIMIT 1
) ck_afname ON true
LEFT JOIN LATERAL (
    SELECT c.status_code, c.status_label
    FROM congestion_areas c
    WHERE c.source = 'capaciteitskaart' AND c.direction = 'invoeding'
      AND ST_Contains(c.geom, g.geom)
    ORDER BY c.status_code DESC NULLS LAST LIMIT 1
) ck_inv ON true
LEFT JOIN LATERAL (
    SELECT c.status_label
    FROM congestion_areas c
    WHERE c.source = 'liander_transportschaarste' AND c.direction = 'afname'
      AND ST_Contains(c.geom, g.geom)
    ORDER BY c.status_label LIMIT 1
) li_afname ON true
LEFT JOIN LATERAL (
    SELECT c.status_label
    FROM congestion_areas c
    WHERE c.source = 'liander_transportschaarste' AND c.direction = 'invoeding'
      AND ST_Contains(c.geom, g.geom)
    ORDER BY c.status_label LIMIT 1
) li_inv ON true;

CREATE UNIQUE INDEX station_congestion_station_id_idx ON station_congestion (station_id);
CREATE INDEX station_congestion_geom_idx ON station_congestion USING GIST (geom);
"""

# Materialized because the 4-way point-in-polygon join is expensive at 80k+
# stations (~1 minute to compute); querying a plain view would redo that
# work on every SELECT. Re-run this script (or REFRESH MATERIALIZED VIEW
# station_congestion) after golden_stations or congestion_areas changes.


def get_connection():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


@with_retries()
def fetch_page(layer_url, offset, page_size):
    response = requests.get(
        f"{layer_url}/query",
        params={
            "where": "1=1",
            "outFields": "*",
            "outSR": 4326,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        },
        headers=HEADERS,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["features"]


def fetch_all(layer_url, page_size=1000):
    features = []
    offset = 0
    while True:
        page = fetch_page(layer_url, offset, page_size)
        features.extend(page)
        if len(page) < page_size:
            return features
        offset += page_size


def main():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Created empty congestion_areas table.")

    fetched_at = datetime.now(timezone.utc)
    for source, direction, layer_url, extract_status, page_size in LAYERS:
        features = fetch_all(layer_url, page_size=page_size)
        rows = []
        for feat in features:
            props = feat.get("properties") or {}
            geom = feat.get("geometry")
            if not geom:
                continue
            status_code, status_label = extract_status(props)
            area_name = props.get("voedingsgebied_naam") or props.get("gemeente")
            operator = props.get("RNB") or ("Liander" if source.startswith("liander") else None)
            rows.append(
                (
                    source,
                    direction,
                    status_code,
                    status_label,
                    area_name,
                    operator,
                    Jsonb(props),
                    Jsonb(geom),
                    fetched_at,
                )
            )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO congestion_areas
                    (source, direction, status_code, status_label, area_name,
                     operator, raw, geom, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        ST_Multi(ST_CollectionExtract(ST_MakeValid(
                            ST_SetSRID(ST_GeomFromGeoJSON(%s::text), 4326)), 3)),
                        %s)
                """,
                rows,
            )
        conn.commit()
        print(f"  {source}/{direction}: {len(rows)} polygons inserted")

    with conn.cursor() as cur:
        cur.execute(CREATE_VIEW_SQL)
    conn.commit()
    print("Created station_congestion view.")
    conn.close()


if __name__ == "__main__":
    main()

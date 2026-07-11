"""Load the NWB Wegvakken shapefile into the `wegvakken` table in PostGIS.

Adapted from NDW's nwb-postgresql approach (shp2pgsql, SRID 28992), but
implemented in pure Python since no GDAL/shp2pgsql binary is installed.
"""

import os
import sys

import psycopg
import shapefile
from dotenv import load_dotenv

load_dotenv()

SHAPEFILE_PATH = "data/nwb/extracted/01-07-2026/Wegvakken/Wegvakken.shp"
SRID = 28992

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS wegvakken;
CREATE TABLE wegvakken (
    wvk_id BIGINT PRIMARY KEY,
    jte_id_beg BIGINT,
    jte_id_end BIGINT,
    stt_naam TEXT,
    wpsnaam TEXT,
    gme_naam TEXT,
    wegnummer TEXT,
    wegbehnaam TEXT,
    bst_code TEXT,
    frc TEXT,
    fow TEXT,
    geom geometry(MultiLineString, 28992)
);
"""

CREATE_INDEX_SQL = "CREATE INDEX wegvakken_geom_idx ON wegvakken USING GIST (geom);"


def shape_to_ewkt(shape):
    """Convert a pyshp PolyLine shape (possibly multi-part) to an EWKT MULTILINESTRING string."""
    parts = list(shape.parts) + [len(shape.points)]
    lines = []
    for i in range(len(parts) - 1):
        pts = shape.points[parts[i] : parts[i + 1]]
        coords = ", ".join(f"{x} {y}" for x, y in pts)
        lines.append(f"({coords})")
    return f"SRID={SRID};MULTILINESTRING({', '.join(lines)})"


def main():
    conn = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )

    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Created empty wegvakken table.")

    sf = shapefile.Reader(SHAPEFILE_PATH, encoding="latin1")
    total = len(sf)
    print(f"Loading {total} road segments...")

    columns = (
        "wvk_id, jte_id_beg, jte_id_end, stt_naam, wpsnaam, gme_naam, "
        "wegnummer, wegbehnaam, bst_code, frc, fow, geom"
    )

    with conn.cursor() as cur:
        with cur.copy(f"COPY wegvakken ({columns}) FROM STDIN") as copy:
            for i, shape_record in enumerate(sf.iterShapeRecords()):
                rec = shape_record.record
                geom_ewkt = shape_to_ewkt(shape_record.shape)
                copy.write_row(
                    (
                        rec["WVK_ID"],
                        rec["JTE_ID_BEG"],
                        rec["JTE_ID_END"],
                        rec["STT_NAAM"] or None,
                        rec["WPSNAAM"] or None,
                        rec["GME_NAAM"] or None,
                        rec["WEGNUMMER"] or None,
                        rec["WEGBEHNAAM"] or None,
                        rec["BST_CODE"] or None,
                        rec["FRC"] or None,
                        rec["FOW"] or None,
                        geom_ewkt,
                    )
                )
                if (i + 1) % 200_000 == 0:
                    print(f"  {i + 1}/{total} loaded...")
    conn.commit()
    print("Data load complete. Building spatial index...")

    with conn.cursor() as cur:
        cur.execute(CREATE_INDEX_SQL)
    conn.commit()
    print("Spatial index created.")

    conn.close()


if __name__ == "__main__":
    sys.exit(main())

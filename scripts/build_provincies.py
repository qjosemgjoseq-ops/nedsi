"""Build the provincies reference table: real bbox + centroid per province,
derived as the envelope of its constituent gemeenten's already-real bboxes
(gemeenten table, itself built from NWB wegvakken geometry).

The gemeente -> provincie assignment comes from CBS StatLine (authoritative,
dataset 86059NED "Gebieden in Nederland", fields Naam_2/Naam_29), saved to
gemeente_provincie_map.json. This replaces the webapp's hand-typed
PROVINCE_CONFIGS bboxes, which were never verified and leaked data across
province borders (e.g. Amsterdam showing up under "Utrecht provincie").
"""

import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

MAP_PATH = Path(__file__).parent / "gemeente_provincie_map.json"

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS provincies;
CREATE TABLE provincies (
    naam TEXT PRIMARY KEY,
    min_lon DOUBLE PRECISION,
    min_lat DOUBLE PRECISION,
    max_lon DOUBLE PRECISION,
    max_lat DOUBLE PRECISION,
    center_lon DOUBLE PRECISION,
    center_lat DOUBLE PRECISION,
    gemeente_count INT,
    wegvak_count INT
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


def main():
    with open(MAP_PATH, encoding="utf-8") as f:
        gem_to_prov_raw: dict[str, str] = json.load(f)
    # CBS disambiguates duplicate gemeente names as "Bergen (L.)"; NWB uses
    # "Bergen (L)" (no period). Normalize so both sides match.
    gem_to_prov = {naam.replace(".)", ")"): prov for naam, prov in gem_to_prov_raw.items()}
    # CBS always disambiguates with a "(XX)" suffix, even for names that are
    # no longer duplicated (e.g. "Beek (L.)") -- but NWB drops the suffix
    # when there's now only one gemeente with that base name. Add a
    # base-name fallback, only when it's unambiguous.
    base_name_counts: dict[str, int] = {}
    base_name_prov: dict[str, str] = {}
    for naam, prov in gem_to_prov.items():
        base = re.sub(r"\s*\([^)]*\)$", "", naam)
        base_name_counts[base] = base_name_counts.get(base, 0) + 1
        base_name_prov[base] = prov
    for base, count in base_name_counts.items():
        if count == 1 and base not in gem_to_prov:
            gem_to_prov[base] = base_name_prov[base]

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT naam, min_lon, min_lat, max_lon, max_lat, wegvak_count FROM gemeenten;")
        gemeenten = cur.fetchall()

    unmatched = []
    provinces: dict[str, dict] = {}
    for naam, min_lon, min_lat, max_lon, max_lat, wegvak_count in gemeenten:
        prov = gem_to_prov.get(naam)
        if prov is None:
            unmatched.append(naam)
            continue
        p = provinces.setdefault(prov, {
            "min_lon": min_lon, "min_lat": min_lat,
            "max_lon": max_lon, "max_lat": max_lat,
            "gemeente_count": 0, "wegvak_count": 0,
        })
        p["min_lon"] = min(p["min_lon"], min_lon)
        p["min_lat"] = min(p["min_lat"], min_lat)
        p["max_lon"] = max(p["max_lon"], max_lon)
        p["max_lat"] = max(p["max_lat"], max_lat)
        p["gemeente_count"] += 1
        p["wegvak_count"] += wegvak_count

    if unmatched:
        print(f"WARNING: {len(unmatched)} gemeenten in NWB have no CBS provincie match: {unmatched}")
        print("These will be EXCLUDED from all province bboxes -- check for name mismatches "
              "(merged/renamed gemeenten) before trusting province boundaries.")

    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        for naam, p in provinces.items():
            center_lon = (p["min_lon"] + p["max_lon"]) / 2
            center_lat = (p["min_lat"] + p["max_lat"]) / 2
            cur.execute(
                """
                INSERT INTO provincies
                    (naam, min_lon, min_lat, max_lon, max_lat, center_lon, center_lat, gemeente_count, wegvak_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (naam, p["min_lon"], p["min_lat"], p["max_lon"], p["max_lat"],
                 center_lon, center_lat, p["gemeente_count"], p["wegvak_count"]),
            )
    conn.commit()

    print(f"Created provincies table with {len(provinces)} provinces.")
    with conn.cursor() as cur:
        cur.execute("SELECT naam, gemeente_count, wegvak_count FROM provincies ORDER BY naam;")
        total_gem = 0
        for row in cur.fetchall():
            print(" ", row)
            total_gem += row[1]
        print(f"Total gemeenten covered: {total_gem} (should be 342 minus any unmatched)")

    conn.close()


if __name__ == "__main__":
    main()

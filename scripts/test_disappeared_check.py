"""Proof-of-concept for the t_disappeared time-series check, using the cached
DOT-NL city snapshots the NEDSI webapp already ships (frontend/public/
mock_ndw_*.json, captured ~2026-06-04) as the OLD point in time, versus the
current raw_ndw_dotnl snapshot as NEW.

Station ids are nationally unique, so "disappeared" = id present in the old
cache but absent from the current DB -- bbox-independent, and safe against the
1000-feature API cap (a truncated cache can only UNDER-count disappearances,
never invent one).
"""

import glob
import json
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = r"C:\nedis\webapp\app\frontend\public"


def connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


# A disappeared id is classified by looking for a CURRENT station at the same
# physical spot: if the location is occupied now, the id churned (re-keying),
# it wasn't physically removed.
NEARBY_SQL = """
SELECT raw->>'id', raw->'properties'->>'operator_name',
       ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS m
FROM raw_ndw_dotnl
WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 30)
ORDER BY m LIMIT 1
"""


def classify(cur, feat):
    """Return ('rekeyed_same'|'rekeyed_other'|'removed', detail)."""
    coords = feat.get("geometry", {}).get("coordinates")
    if not coords or coords == [0, 0]:
        return "removed", "geen coord"
    lon, lat = coords[0], coords[1]
    cur.execute(NEARBY_SQL, (lon, lat, lon, lat))
    row = cur.fetchone()
    if not row:
        return "removed", None
    new_id, new_op, dist = row
    old_op = feat.get("properties", {}).get("operator_name")
    same = (new_op or "") == (old_op or "")
    return ("rekeyed_same" if same else "rekeyed_other"), f"{new_id} ({new_op}, {dist:.0f}m)"


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT raw->>'id' FROM raw_ndw_dotnl")
    current_ids = {r[0] for r in cur.fetchall()}
    print(f"Current DOT-NL snapshot: {len(current_ids)} stations\n")

    tot = {"old": 0, "gone": 0, "rekeyed_same": 0, "rekeyed_other": 0, "removed": 0}
    removed_examples = []
    for path in sorted(glob.glob(os.path.join(CACHE_DIR, "mock_ndw_*.json"))):
        city = os.path.basename(path).replace("mock_ndw_", "").replace(".json", "")
        feats = json.load(open(path, encoding="utf-8-sig")).get("features", [])
        by_id = {f.get("id"): f for f in feats if f.get("id")}
        capped = " (capped)" if len(feats) >= 1000 else ""
        gone = set(by_id) - current_ids

        c = {"rekeyed_same": 0, "rekeyed_other": 0, "removed": 0}
        for gid in gone:
            kind, detail = classify(cur, by_id[gid])
            c[kind] += 1
            if kind == "removed":
                p = by_id[gid].get("properties", {})
                removed_examples.append(f"{city}: {p.get('operator_name')} - {p.get('address')}")
        tot["old"] += len(by_id)
        tot["gone"] += len(gone)
        for k in c:
            tot[k] += c[k]
        print(f"{city:9} old={len(by_id):4}{capped}  gone={len(gone):3}  ->  "
              f"re-keyed(zelfde op)={c['rekeyed_same']:3}  re-keyed(ander op)={c['rekeyed_other']:2}  "
              f"ECHT WEG={c['removed']:3}")

    print(f"\nTOTAAL {tot['gone']} verdwenen id's van {tot['old']}:")
    print(f"  re-keyed, zelfde operator (id-churn, NIET weg):  {tot['rekeyed_same']}")
    print(f"  re-keyed, andere operator (plek overgenomen):    {tot['rekeyed_other']}")
    print(f"  ECHT verdwenen (geen station meer op die plek):  {tot['removed']}")
    real = tot["removed"]
    print(f"\n=> Van {tot['gone']} 'verdwenen' id's is er maar ~{real} een echte verwijdering "
          f"({100*real/tot['gone']:.0f}%); de rest is id-churn.")
    print("\nVoorbeelden van ECHTE verwijderingen:")
    for ex in removed_examples[:10]:
        print(f"  - {ex}")
    conn.close()


if __name__ == "__main__":
    main()

"""Persist the id-stability / disappeared-station analysis into tables the NEDSI
webapp serves. Compares the cached DOT-NL city snapshots
(frontend/public/mock_ndw_*.json, ~2026-06-04) against the current
raw_ndw_dotnl snapshot, classifying each vanished id as re-keying (a station
still at the same spot) vs a true removal.

SAMPLE ONLY: 4 gemeenten (Utrecht/Amsterdam/Rotterdam/Den Haag), ~5-6 weeks.
This is a proof-of-concept for the time-series checks until daily national
snapshot history exists -- the webapp must label it as a steekproef, not fold
it into the national score.

Writes:
  datakwaliteit_idchurn_summary   - one row: totals + sample metadata
  datakwaliteit_idchurn_operator  - per operator: old / rekeyed / removed
"""

import glob
import json
import os
from collections import defaultdict
from datetime import date

import psycopg
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = r"C:\nedis\webapp\app\frontend\public"

NEARBY_SQL = """
SELECT raw->'properties'->>'operator_name'
FROM raw_ndw_dotnl
WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 30)
ORDER BY ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
LIMIT 1
"""

DDL = """
DROP TABLE IF EXISTS datakwaliteit_idchurn_summary;
CREATE TABLE datakwaliteit_idchurn_summary (
    id INTEGER PRIMARY KEY DEFAULT 1,
    sample_cities INTEGER, old_total INTEGER, gone INTEGER,
    rekeyed_same INTEGER, rekeyed_other INTEGER, removed INTEGER,
    old_date DATE, new_date DATE, snapshot_date DATE
);
DROP TABLE IF EXISTS datakwaliteit_idchurn_operator;
CREATE TABLE datakwaliteit_idchurn_operator (
    operator TEXT PRIMARY KEY,
    old_count INTEGER, rekeyed INTEGER, removed INTEGER
);
"""


def connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT raw->>'id' FROM raw_ndw_dotnl")
    current_ids = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT max((raw->'properties'->>'last_updated'))::date FROM raw_ndw_dotnl")
    new_date = cur.fetchone()[0]

    per_op = defaultdict(lambda: {"old": 0, "rekeyed": 0, "removed": 0})
    tot = {"old": 0, "gone": 0, "rekeyed_same": 0, "rekeyed_other": 0, "removed": 0}
    old_dates = []
    n_cities = 0

    for path in sorted(glob.glob(os.path.join(CACHE_DIR, "mock_ndw_*.json"))):
        n_cities += 1
        feats = json.load(open(path, encoding="utf-8-sig")).get("features", [])
        for f in feats:
            lu = f.get("properties", {}).get("last_updated")
            if lu:
                old_dates.append(lu[:10])
        by_id = {f.get("id"): f for f in feats if f.get("id")}
        for op_feat in by_id.values():
            per_op[op_feat.get("properties", {}).get("operator_name") or "—"]["old"] += 1
        tot["old"] += len(by_id)
        for gid in set(by_id) - current_ids:
            tot["gone"] += 1
            feat = by_id[gid]
            op = feat.get("properties", {}).get("operator_name") or "—"
            coords = feat.get("geometry", {}).get("coordinates")
            kind = "removed"
            if coords and coords != [0, 0]:
                cur.execute(NEARBY_SQL, (coords[0], coords[1], coords[0], coords[1]))
                row = cur.fetchone()
                if row:
                    new_op = row[0]
                    same = (new_op or "") == (op or "")
                    kind = "rekeyed_same" if same else "rekeyed_other"
            if kind == "removed":
                tot["removed"] += 1
                per_op[op]["removed"] += 1
            else:
                tot[kind] += 1
                per_op[op]["rekeyed"] += 1

    cur.execute(DDL)
    old_date = max(old_dates) if old_dates else None  # newest last_updated in the cache
    cur.execute(
        "INSERT INTO datakwaliteit_idchurn_summary "
        "(id, sample_cities, old_total, gone, rekeyed_same, rekeyed_other, removed, old_date, new_date, snapshot_date)"
        " VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (n_cities, tot["old"], tot["gone"], tot["rekeyed_same"], tot["rekeyed_other"],
         tot["removed"], old_date, new_date, date.today()),
    )
    for op, d in per_op.items():
        if d["rekeyed"] or d["removed"]:
            cur.execute(
                "INSERT INTO datakwaliteit_idchurn_operator (operator, old_count, rekeyed, removed)"
                " VALUES (%s,%s,%s,%s)", (op, d["old"], d["rekeyed"], d["removed"]),
            )
    conn.commit()
    conn.close()
    print(f"Loaded id-churn: {n_cities} cities, {tot['gone']} gone "
          f"({tot['rekeyed_same']+tot['rekeyed_other']} re-keyed, {tot['removed']} removed), "
          f"old~{old_date} new~{new_date}.")


if __name__ == "__main__":
    main()

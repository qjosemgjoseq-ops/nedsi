"""Inductive data-quality profiling of the DOT-NL snapshot: instead of only
checking for issues we already thought of, profile every field and every
cross-field relationship and let the anomalies surface checks we haven't
named yet. Output feeds the DOT-NL data-quality check catalog.

Grouped by the standard DQ dimensions:
  COMPLETENESS  - is the field present / populated?
  VALIDITY      - does the value conform to its type / enum / range / standard?
  ACCURACY      - does the value match reality (cross-source / geography)?
  CONSISTENCY   - do related fields agree with each other?
  UNIQUENESS    - are there duplicates that shouldn't exist?
  TIMELINESS    - is the data fresh?
"""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

# NL mainland bounding box (rough) for a plausibility check.
NL_BBOX = (3.2, 50.7, 7.3, 53.6)


def connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def scalar(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()[0]


def main():
    conn = connect()
    cur = conn.cursor()
    total = scalar(cur, "SELECT count(*) FROM raw_ndw_dotnl")
    P = "raw->'properties'->"  # jsonb path helper

    def pct(n):
        return f"{n:6}  ({100*n/total:5.1f}%)"

    print(f"DOT-NL stations profiled: {total}\n")

    print("== COMPLETENESS: missing / empty fields ==")
    checks = {
        "operator_name missing":       f"SELECT count(*) FROM raw_ndw_dotnl WHERE {P}>'operator_name' IS NULL",
        "owner_name missing":          f"SELECT count(*) FROM raw_ndw_dotnl WHERE {P}>'owner_name' IS NULL",
        "address missing/empty":       f"SELECT count(*) FROM raw_ndw_dotnl WHERE coalesce({P}>'address','')=''",
        "no availabilities array":     f"SELECT count(*) FROM raw_ndw_dotnl WHERE {P}'availabilities' IS NULL OR jsonb_array_length({P}'availabilities')=0",
        "last_updated missing":        f"SELECT count(*) FROM raw_ndw_dotnl WHERE {P}>'last_updated' IS NULL",
        "no tariff_ids on any connector": f"SELECT count(*) FROM raw_ndw_dotnl t WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements({P}'availabilities') a WHERE jsonb_array_length(coalesce(a->'tariff_ids','[]'))>0)",
    }
    for label, sql in checks.items():
        print(f"  {label:34} {pct(scalar(cur, sql))}")

    print("\n== VALIDITY: values out of range / malformed ==")
    v = {
        "coordinates (0,0)":           "SELECT count(*) FROM raw_ndw_dotnl WHERE ST_X(geom)=0 AND ST_Y(geom)=0",
        "outside NL bbox":             f"SELECT count(*) FROM raw_ndw_dotnl WHERE NOT (ST_X(geom) BETWEEN {NL_BBOX[0]} AND {NL_BBOX[2]} AND ST_Y(geom) BETWEEN {NL_BBOX[1]} AND {NL_BBOX[3]})",
        "connector power_max = 0/null": f"SELECT count(*) FROM raw_ndw_dotnl t WHERE EXISTS (SELECT 1 FROM jsonb_array_elements({P}'availabilities') a WHERE coalesce((a->>'power_max')::float,0)=0)",
        "power_max > 400kW (implausible)": f"SELECT count(*) FROM raw_ndw_dotnl t WHERE EXISTS (SELECT 1 FROM jsonb_array_elements({P}'availabilities') a WHERE (a->>'power_max')::float > 400000)",
        "country <> NLD":              f"SELECT count(*) FROM raw_ndw_dotnl WHERE {P}>'country' <> 'NLD'",
    }
    for label, sql in v.items():
        print(f"  {label:34} {pct(scalar(cur, sql))}")

    print("\n== CONSISTENCY: related fields contradict ==")
    c = {
        "available > total (impossible)": f"SELECT count(*) FROM raw_ndw_dotnl t WHERE EXISTS (SELECT 1 FROM jsonb_array_elements({P}'availabilities') a WHERE (a->>'available')::int > (a->>'total')::int)",
        "total connectors = 0":        f"SELECT count(*) FROM raw_ndw_dotnl t WHERE EXISTS (SELECT 1 FROM jsonb_array_elements({P}'availabilities') a WHERE (a->>'total')::int = 0)",
        "open=false but availability>0": f"SELECT count(*) FROM raw_ndw_dotnl t WHERE {P}>'open'='false' AND EXISTS (SELECT 1 FROM jsonb_array_elements({P}'availabilities') a WHERE (a->>'available')::int > 0)",
        "id-prefix cpo <> cpo_id field": f"SELECT count(*) FROM raw_ndw_dotnl WHERE upper(split_part(raw->>'id','-',2)) <> upper({P}>'cpo_id')",
    }
    for label, sql in c.items():
        print(f"  {label:34} {pct(scalar(cur, sql))}")

    print("\n== UNIQUENESS: duplicates ==")
    dup_ids = scalar(cur, "SELECT count(*) FROM (SELECT raw->>'id' FROM raw_ndw_dotnl GROUP BY 1 HAVING count(*)>1) x")
    dup_coords = scalar(cur, "SELECT count(*) FROM (SELECT ST_X(geom), ST_Y(geom) FROM raw_ndw_dotnl WHERE NOT (ST_X(geom)=0 AND ST_Y(geom)=0) GROUP BY 1,2 HAVING count(*)>1) x")
    print(f"  {'duplicate station ids':34} {dup_ids:6}")
    print(f"  {'coordinates shared by >1 station':34} {dup_coords:6}")

    print("\n== TIMELINESS: staleness of last_updated ==")
    for label, days in [("older than 7 days", 7), ("older than 30 days", 30), ("older than 90 days", 90)]:
        n = scalar(cur, f"SELECT count(*) FROM raw_ndw_dotnl WHERE ({P}>'last_updated')::timestamptz < now() - interval '{days} days'")
        print(f"  {label:34} {pct(n)}")

    print("\n== CARDINALITY probes (spot the long tail) ==")
    for field in ("power_type", "connector_type", "connector_format"):
        cur.execute(f"SELECT a->>'{field}', count(*) FROM raw_ndw_dotnl t, jsonb_array_elements({P}'availabilities') a GROUP BY 1 ORDER BY 2 DESC")
        rows = cur.fetchall()
        vals = ", ".join(f"{k}:{n}" for k, n in rows[:8])
        print(f"  {field} ({len(rows)} distinct): {vals}")

    conn.close()


if __name__ == "__main__":
    main()

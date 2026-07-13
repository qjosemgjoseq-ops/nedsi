"""Compute the DOT-NL datakwaliteit scorecard and persist it to two tables the
NEDSI webapp serves:

  datakwaliteit_scorecard  - one row per data-providing OCPI party: score,
                             station count, and each check's fail count + pct.
  datakwaliteit_summary    - one row per check: dimension, severity, national
                             affected count + pct (the check catalog, with live
                             numbers).

Same check logic as build_cpo_scorecard.py -- kept in one place so the report
and the webapp can never disagree. Re-run after each DOT-NL snapshot refresh.
"""

import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

load_dotenv()

# check_id -> (weight, dimension, severity, definition_nl, afir_required)
# afir_required: does AFIR (EU 2023/1804) oblige this field via the national
# access point? AFIR-scope gaps are compliance findings, not quality nitpicks.
# Only owner_name is genuinely optional. Exact AFIR annex field-scope to be
# confirmed with NDW. See project_linda_dotnl_afir_distinction memory.
CHECKS = {
    "available_gt_total": (30, "consistentie", "kritiek", "available > total (onmogelijk)", True),
    "missing_tariff": (25, "volledigheid", "hoog", "geen tariff_ids op enige connector (geen prijs af te leiden)", True),
    "stale_30d": (15, "tijdigheid", "hoog", "last_updated ouder dan 30 dagen", True),
    "total_zero": (15, "consistentie", "middel", "total connectoren = 0", True),
    "missing_operator": (10, "volledigheid", "middel", "operator_name ontbreekt", True),
    "power_zero": (10, "validiteit", "middel", "power_max = 0/null op een connector", True),
    "power_implausible": (10, "validiteit", "middel", "power_max > 400 kW (onrealistisch)", True),
    "missing_owner": (8, "volledigheid", "laag", "owner_name ontbreekt (niet AFIR-verplicht)", False),
}


def connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def party_of(fid):
    m = re.match(r"^([A-Za-z]{2})[-*]([A-Za-z0-9]{2,4})[-*]", fid or "")
    return f"{m.group(1).upper()}-{m.group(2).upper()}" if m else None


def station_flags(props, now):
    avails = props.get("availabilities") or []
    powers = [a.get("power_max") for a in avails]
    totals = [a.get("total") for a in avails]
    avail = [a.get("available") for a in avails]
    last = props.get("last_updated")
    stale_days = None
    if last:
        try:
            stale_days = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
        except ValueError:
            pass
    return {
        "missing_tariff": not any((a.get("tariff_ids") or []) for a in avails),
        "missing_operator": not props.get("operator_name"),
        "missing_owner": not props.get("owner_name"),
        "power_zero": any((p or 0) == 0 for p in powers),
        "power_implausible": any((p or 0) > 400000 for p in powers),
        "available_gt_total": any(a is not None and t is not None and a > t for a, t in zip(avail, totals)),
        "total_zero": any((t or 0) == 0 for t in totals),
        "stale_30d": stale_days is not None and stale_days > 30,
    }


DDL = """
DROP TABLE IF EXISTS datakwaliteit_scorecard;
CREATE TABLE datakwaliteit_scorecard (
    party_id TEXT PRIMARY KEY,
    operator TEXT,
    stations INTEGER,
    score DOUBLE PRECISION,
    checks JSONB,          -- {check_id: {count, pct}}
    snapshot_date DATE
);
DROP TABLE IF EXISTS datakwaliteit_summary;
CREATE TABLE datakwaliteit_summary (
    check_id TEXT PRIMARY KEY,
    dimension TEXT,
    severity TEXT,
    definition TEXT,
    weight INTEGER,
    afir_required BOOLEAN,
    affected INTEGER,
    total INTEGER,
    pct DOUBLE PRECISION,
    snapshot_date DATE
);
"""


def main():
    now = datetime.now(timezone.utc)
    today = now.date()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT raw->>'id', raw->'properties' FROM raw_ndw_dotnl")

    agg = defaultdict(lambda: {"n": 0, "name": None, **{k: 0 for k in CHECKS}})
    national = {k: 0 for k in CHECKS}
    total = 0
    for fid, props in cur.fetchall():
        party = party_of(fid)
        if not party:
            continue
        total += 1
        a = agg[party]
        a["n"] += 1
        if not a["name"]:
            a["name"] = props.get("operator_name")
        for k, v in station_flags(props, now).items():
            if v:
                a[k] += 1
                national[k] += 1

    cur.execute(DDL)

    import json
    for party, a in agg.items():
        n = a["n"]
        score = 100.0
        checks = {}
        for k, (w, _dim, _sev, _defn, afir) in CHECKS.items():
            cnt = a[k]
            frac = cnt / n
            score -= w * frac
            checks[k] = {"count": cnt, "pct": round(100 * frac, 1), "afir": afir}
        cur.execute(
            "INSERT INTO datakwaliteit_scorecard (party_id, operator, stations, score, checks, snapshot_date)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (party, a["name"], n, round(max(0, score), 1), json.dumps(checks), today),
        )

    for k, (w, dim, sev, defn, afir) in CHECKS.items():
        cnt = national[k]
        cur.execute(
            "INSERT INTO datakwaliteit_summary (check_id, dimension, severity, definition, weight, afir_required, affected, total, pct, snapshot_date)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (k, dim, sev, defn, w, afir, cnt, total, round(100 * cnt / total, 1), today),
        )

    conn.commit()
    conn.close()
    print(f"Loaded scorecard for {len(agg)} parties, {total} stations, snapshot {today}.")


if __name__ == "__main__":
    main()

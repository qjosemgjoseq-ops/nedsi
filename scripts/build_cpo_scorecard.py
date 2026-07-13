"""Per-CPO datakwaliteit-scorecard for DOT-NL. For every data-providing OCPI
party (the id-prefix party that submits to DOT-NL), compute each confirmed
check from DOT_NL_DATAKWALITEIT_CHECKLIST.md as a fail-rate, then a composite
0-100 quality score. This turns the check catalog into "which supplier to talk
to, and about what".

Grouping unit = submitting party-id (who NDW has the OCPI relationship with and
who can actually fix the feed), labelled with the dominant operator_name.

Scoring is transparent and provisional (weights to be confirmed with DOT-team):
score starts at 100, each check subtracts weight * fail_fraction. Only
per-station checks that need no snapshot history or external source are scored
here; time-series/accuracy checks (t_disappeared, t_coord_far_from_road, ...)
are added once snapshot history exists.
"""

import csv
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

load_dotenv()

OUTPUT_CSV = r"C:\nedis\cpo_scorecard.csv"

# check_id -> (weight, severity). Weight = max score points lost if 100% fail.
WEIGHTS = {
    "missing_tariff": (25, "hoog (AFIR)"),
    "stale_30d": (15, "hoog"),
    "missing_operator": (10, "middel"),
    "missing_owner": (8, "middel"),
    "power_zero": (10, "middel"),
    "power_implausible": (10, "middel"),
    "available_gt_total": (30, "kritiek"),
    "total_zero": (15, "middel"),
}


def connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def party_of(fid: str) -> str | None:
    m = re.match(r"^([A-Za-z]{2})[-*]([A-Za-z0-9]{2,4})[-*]", fid or "")
    return f"{m.group(1).upper()}-{m.group(2).upper()}" if m else None


def station_flags(props: dict, now: datetime) -> dict:
    avails = props.get("availabilities") or []
    powers = [a.get("power_max") for a in avails]
    totals = [a.get("total") for a in avails]
    avails_avail = [a.get("available") for a in avails]
    has_any_tariff = any((a.get("tariff_ids") or []) for a in avails)

    last = props.get("last_updated")
    stale_days = None
    if last:
        try:
            dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            stale_days = (now - dt).days
        except ValueError:
            pass

    return {
        "missing_tariff": not has_any_tariff,
        "missing_operator": not props.get("operator_name"),
        "missing_owner": not props.get("owner_name"),
        "power_zero": any((p or 0) == 0 for p in powers),
        "power_implausible": any((p or 0) > 400000 for p in powers),
        "available_gt_total": any(
            (av is not None and tt is not None and av > tt)
            for av, tt in zip(avails_avail, totals)
        ),
        "total_zero": any((t or 0) == 0 for t in totals),
        "stale_30d": stale_days is not None and stale_days > 30,
    }


def main():
    now = datetime.now(timezone.utc)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT raw->>'id', raw->'properties' FROM raw_ndw_dotnl")

    agg = defaultdict(lambda: {"n": 0, "name": None, **{k: 0 for k in WEIGHTS}})
    for fid, props in cur.fetchall():
        party = party_of(fid)
        if not party:
            continue
        a = agg[party]
        a["n"] += 1
        if not a["name"]:
            a["name"] = props.get("operator_name")
        for k, v in station_flags(props, now).items():
            if k in WEIGHTS and v:
                a[k] += 1
    conn.close()

    rows = []
    for party, a in agg.items():
        n = a["n"]
        score = 100.0
        rates = {}
        for k, (w, _sev) in WEIGHTS.items():
            frac = a[k] / n
            rates[k] = frac
            score -= w * frac
        rows.append((round(max(0, score), 1), party, a["name"], n, rates, a))

    rows.sort(key=lambda r: (r[0], -r[3]))  # worst score first, then bigger CPO

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        header = ["Score", "Party-ID", "Operator", "Stations"] + [f"%{k}" for k in WEIGHTS]
        w.writerow(header)
        for score, party, name, n, rates, a in rows:
            w.writerow([score, party, name, n] + [f"{100*rates[k]:.1f}" for k in WEIGHTS])

    print(f"Per-CPO datakwaliteit-scorecard  ({len(rows)} data-providing parties)\n")
    print(f"{'Score':>5}  {'Party':10} {'Operator':22} {'Stat.':>6}  "
          f"{'%noTar':>7}{'%stale':>7}{'%noOp':>6}{'%noOwn':>7}{'%pw0':>6}{'%pw!':>6}{'%a>t':>6}{'%t0':>5}")
    print("-" * 108)
    for score, party, name, n, rates, a in rows:
        nm = (name or "?")[:22]
        print(f"{score:5.1f}  {party:10} {nm:22} {n:6}  "
              f"{100*rates['missing_tariff']:6.1f} {100*rates['stale_30d']:6.1f} "
              f"{100*rates['missing_operator']:5.1f} {100*rates['missing_owner']:6.1f} "
              f"{100*rates['power_zero']:5.1f} {100*rates['power_implausible']:5.1f} "
              f"{100*rates['available_gt_total']:5.1f} {100*rates['total_zero']:4.1f}")
    print(f"\nWritten to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

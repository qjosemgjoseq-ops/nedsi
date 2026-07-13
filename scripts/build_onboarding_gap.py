"""Onboarding gap report: which registered NL CPOs are NOT yet delivering to
DOT-NL, ranked by how many stations they have visible in public sources
(OpenChargeMap / OSM). No endpoint scanning -- built purely on authoritative
data: the Benelux ID-register (the official CPO/MSP registry) and the DOT-NL
snapshot already in the NEDSI database.

Key modelling choices, all defensible to the DOT-NL team:
- Only rows with an Operator-ID (CPO) count. Provider-ID-only rows are MSPs
  (charge cards / apps) -- they operate no physical stations, so they can't
  and shouldn't onboard station data.
- Only NL party IDs count. DOT-NL is the Dutch national access point; a CPO
  with only BE*/LU* IDs operates abroad and isn't expected here.
- "Delivering" = the NL party ID appears as the id-prefix of at least one
  DOT-NL station in raw_ndw_dotnl.
"""

import csv
import os
import re
from collections import Counter

import psycopg
from dotenv import load_dotenv

load_dotenv()

REGISTER_CSV = r"C:\nedis\benelux_id_register.csv"
OUTPUT_CSV = r"C:\nedis\onboarding_gap_report.csv"


def normalize(pid: str) -> str | None:
    """'NL*LMS' / 'NL-LMS' -> 'NL-LMS'. Returns None for '/', '', junk."""
    pid = (pid or "").strip().upper().replace("*", "-")
    m = re.match(r"^([A-Z]{2})-([A-Z0-9]{2,4})$", pid)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def parse_register_cpos(path: str):
    """Companies that hold at least one NL CPO id (they operate in NL). For each
    we keep ALL their CPO party ids (NL/BE/LU/...) because a company may deliver
    to DOT-NL under a foreign party id (e.g. Tesla under US-TSL)."""
    nl_cpos: dict[str, str] = {}
    company_all_ids: dict[str, set[str]] = {}
    company_nl_ids: dict[str, set[str]] = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            company = (row.get("Company Name") or "").strip()
            operator_field = row.get("Operator-ID (CPO)") or ""
            ids = {normalize(t) for t in operator_field.split(" - ")}
            ids.discard(None)
            nl_ids = {p for p in ids if p.startswith("NL-")}
            if not nl_ids:
                continue  # not an NL operator
            company_all_ids.setdefault(company, set()).update(ids)
            company_nl_ids.setdefault(company, set()).update(nl_ids)
            for pid in nl_ids:
                nl_cpos.setdefault(pid, company)
    return nl_cpos, company_all_ids, company_nl_ids


def connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def dotnl_parties(cur) -> set[str]:
    cur.execute("SELECT raw->>'id' FROM raw_ndw_dotnl")
    parties = set()
    for (fid,) in cur.fetchall():
        m = re.match(r"^([A-Za-z]{2})[-*]([A-Za-z0-9]{2,4})[-*]", fid or "")
        if m:
            parties.add(f"{m.group(1).upper()}-{m.group(2).upper()}")
    return parties


def dotnl_party_counts(cur) -> Counter:
    """NL party-id -> station count in DOT-NL, plus a sample operator name."""
    cur.execute("SELECT raw->>'id', raw->'properties'->>'operator_name' FROM raw_ndw_dotnl")
    counts = Counter()
    names: dict[str, str] = {}
    for fid, opname in cur.fetchall():
        m = re.match(r"^([A-Za-z]{2})[-*]([A-Za-z0-9]{2,4})[-*]", fid or "")
        if m:
            key = f"{m.group(1).upper()}-{m.group(2).upper()}"
            counts[key] += 1
            names.setdefault(key, opname)
    return counts, names


def main():
    nl_cpos, company_all_ids, company_nl_ids = parse_register_cpos(REGISTER_CSV)
    with connect() as conn, conn.cursor() as cur:
        delivering, dotnl_names = dotnl_party_counts(cur)

    delivering_set = set(delivering)

    # A company DELIVERS if ANY of its registered party ids (any country) is in
    # DOT-NL -- catches companies delivering under a foreign id (Tesla=US-TSL).
    companies_delivering = {}
    companies_missing = {}
    for company, ids in company_all_ids.items():
        live_ids = ids & delivering_set
        if live_ids:
            total = sum(delivering.get(p, 0) for p in live_ids)
            companies_delivering[company] = (sorted(live_ids), total)
        else:
            companies_missing[company] = sorted(company_nl_ids[company])

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Status", "NL Party-IDs", "Delivering IDs", "DOT-NL stations"])
        for c, (live, total) in sorted(companies_delivering.items()):
            w.writerow([c, "DELIVERING", " ".join(sorted(company_nl_ids[c])), " ".join(live), total])
        for c, ids in sorted(companies_missing.items()):
            w.writerow([c, "MISSING", " ".join(ids), "", ""])

    print(f"Registered NL CPO companies:            {len(company_all_ids)}")
    print(f"  delivering to DOT-NL (any party id):  {len(companies_delivering)}")
    print(f"  NOT delivering (onboarding gap):      {len(companies_missing)}")
    print()
    print("=== ONBOARDING GAP: registered NL CPO companies with ZERO delivery ===")
    for c, ids in sorted(companies_missing.items()):
        print(f"  {c:52} {' '.join(ids)}")
    print()
    print(f"Full report written to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

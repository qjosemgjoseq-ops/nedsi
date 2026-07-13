"""Triage the onboarding gap (build_onboarding_gap.py output) into an actionable
target list for the DOT-NL team, using two evidence signals:

  A) Public-station presence in OpenStreetMap (free-text operator tags).
     A missing company that clearly operates public stations in NL is a REAL
     onboarding target -- their data exists, it's just not in DOT-NL yet.
  B) Company-type keywords, to explain the likely NON-targets: lease/fleet,
     fuel-card/MSP, retail, hotel, logistics, and car OEMs -- entities that
     hold a CPO id but don't run a public AFIR-relevant charging network.

This is a first-pass triage to be confirmed by DOT-NL, not a final verdict:
OSM coverage is incomplete, so "no OSM match" means "no public evidence
found", not "operates nothing".
"""

import csv
import os
import re
from collections import defaultdict

import psycopg
from dotenv import load_dotenv

load_dotenv()

GAP_CSV = r"C:\nedis\onboarding_gap_report.csv"
OUTPUT_CSV = r"C:\nedis\onboarding_targets.csv"

LEGAL = re.compile(
    r"\b(b\.?v\.?|n\.?v\.?|gmbh|s\.?a\.?|s\.?a\.?r\.?l\.?|sas|ltd|holding|"
    r"nederland|netherlands|europe|benelux|group|co|kg|se|ag|aps|srl)\b",
    re.I,
)

# Keyword -> category for entities that hold a CPO id but aren't a public CPO.
NON_CPO = {
    "car OEM (own-app charging)": ["tesla", "nio", "volkswagen", "porsche", "volvo",
                                    "lucid", "vinfast", "scania", "einride"],
    "lease / fleet": ["athlon", "arval", "alphabet", "leaseplan"],
    "fuel-card / MSP": ["dkv", "mkb brandstof", "travelcard", "radius", "shuttel",
                        "xximo", "multi tank", "trafineo", "wex", "payter", "digital charging"],
    "retail / supermarket": ["aldi", "lidl", "g&v", "servicestations"],
    "hotel / hospitality": ["van der valk", "tui"],
    "logistics / post": ["dhl", "bpost", "bus de polder"],
    "fuel retailer": ["avia", "varo", "kuwait", "q8", "bp europa", "belgian shell", "dats"],
}


def norm(name: str) -> str:
    n = name.lower()
    n = LEGAL.sub(" ", n)
    n = re.sub(r"[^a-z0-9]+", "", n)
    return n


def connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def dotnl_operator_names(cur) -> dict[str, int]:
    """Normalized operator_name/owner_name in DOT-NL -> station count. This
    catches brands that deliver via a CPMS platform's party id (e.g.
    TotalEnergies via GreenFlux, EQUANS via LastMileSolutions): the register
    party-id doesn't match, but the brand IS present in the feed."""
    idx = defaultdict(int)
    for field in ("operator_name", "owner_name"):
        cur.execute(
            f"SELECT raw->'properties'->>'{field}', count(*) FROM raw_ndw_dotnl "
            f"WHERE raw->'properties'->>'{field}' IS NOT NULL GROUP BY 1"
        )
        for name, n in cur.fetchall():
            key = norm(name)
            if len(key) >= 4:
                idx[key] = max(idx[key], n)
    return idx


def name_in_dotnl(company: str, idx: dict[str, int]) -> int:
    c = norm(company)
    if len(c) < 4:
        return 0
    for key, n in idx.items():
        if c == key or c in key or key in c:
            return n
    return 0


def osm_operator_counts(cur) -> dict[str, int]:
    cur.execute(
        "SELECT raw->'tags'->>'operator', count(*) FROM raw_osm_charging "
        "WHERE raw->'tags'->>'operator' IS NOT NULL GROUP BY 1"
    )
    idx = defaultdict(int)
    for name, n in cur.fetchall():
        for part in re.split(r"[;,]", name):
            key = norm(part)
            if len(key) >= 4:
                idx[key] += n
    return idx


def osm_footprint(company: str, idx: dict[str, int]) -> int:
    c = norm(company)
    if len(c) < 4:
        return 0
    best = 0
    for key, n in idx.items():
        if c == key or c in key or key in c:
            best = max(best, n)
    return best


def category(company: str) -> str | None:
    c = company.lower()
    for cat, kws in NON_CPO.items():
        if any(kw in c for kw in kws):
            return cat
    return None


def main():
    missing = []
    with open(GAP_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Status"] == "MISSING":
                missing.append((row["Company"], row["NL Party-IDs"]))

    with connect() as conn, conn.cursor() as cur:
        osm_idx = osm_operator_counts(cur)
        name_idx = dotnl_operator_names(cur)

    via_platform, priority, likely_non_cpo, unknown = [], [], [], []
    for company, ids in missing:
        in_feed = name_in_dotnl(company, name_idx)
        if in_feed > 0:
            via_platform.append((in_feed, company, ids))
            continue
        fp = osm_footprint(company, osm_idx)
        cat = category(company)
        if fp > 0:
            priority.append((fp, company, ids, cat or "operating CPO"))
        elif cat:
            likely_non_cpo.append((company, ids, cat))
        else:
            unknown.append((company, ids))

    via_platform.sort(reverse=True)
    priority.sort(reverse=True)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Bucket", "Company", "NL Party-IDs", "Stations", "Note"])
        for n, c, ids in via_platform:
            w.writerow(["ALREADY IN DOT-NL VIA PLATFORM", c, ids, n, "brand name present in feed"])
        for fp, c, ids, note in priority:
            w.writerow(["PRIORITY TARGET", c, ids, fp, note])
        for c, ids in unknown:
            w.writerow(["REVIEW (no public evidence)", c, ids, "", ""])
        for c, ids, cat in sorted(likely_non_cpo):
            w.writerow(["LIKELY NOT A PUBLIC CPO", c, ids, "", cat])

    print(f"Onboarding gap classified ({len(missing)} companies):\n")
    print(f"  ALREADY IN DOT-NL via a platform party-id: {len(via_platform)}  (phantom gaps)")
    print(f"  PRIORITY TARGET (public stations in OSM):  {len(priority)}")
    print(f"  REVIEW (real CPO likely, no public proof): {len(unknown)}")
    print(f"  LIKELY NOT A PUBLIC CPO (typed):           {len(likely_non_cpo)}")
    print(f"  --> TRUE onboarding candidates:            {len(priority) + len(unknown)}")
    print()
    print("=== ALREADY IN DOT-NL VIA A PLATFORM (remove from gap; ID-mismatch only) ===")
    for n, c, ids in via_platform:
        print(f"  {n:6}  {c:42} {ids}")
    print()
    print("=== PRIORITY TARGETS (operating publicly, brand NOT in DOT-NL feed) ===")
    for fp, c, ids, note in priority:
        print(f"  {fp:5}  {c:42} {ids:20} {note}")
    print()
    print("=== LIKELY NOT A PUBLIC CPO (exclude from outreach) ===")
    for c, ids, cat in sorted(likely_non_cpo):
        print(f"  {c:42} {cat}")
    print()
    print("=== REVIEW: no public evidence found (confirm manually) ===")
    for c, ids in sorted(unknown):
        print(f"  {c:42} {ids}")
    print(f"\nWritten to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

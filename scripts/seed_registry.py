"""One-time seed of the station registry so the id-stability monitor has real
history from day one instead of starting empty:

  1. plant the 4 cached city snapshots (~2026-06-04) as backdated stations,
  2. lay the current national raw_ndw_dotnl snapshot on top (advances last_seen
     for still-present ids, adds the rest nationally),
  3. run churn detection -> the vanished cache ids get classified (re-keying vs
     removal) and logged as dated events.

After this, the GitHub Actions cron keeps the registry and event log growing
nationally and unattended.
"""

import glob
import json
import os
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv
from dotnl_registry import detect_churn, ensure_tables, get_connection, upsert_registry

load_dotenv()

CACHE_DIR = r"C:\nedis\webapp\app\frontend\public"


def load_cache_features():
    feats = []
    max_lu = ""
    for path in sorted(glob.glob(os.path.join(CACHE_DIR, "mock_ndw_*.json"))):
        data = json.load(open(path, encoding="utf-8-sig"))
        for f in data.get("features", []):
            feats.append(f)
            lu = f.get("properties", {}).get("last_updated")
            if lu and lu > max_lu:
                max_lu = lu
    anchor = datetime.fromisoformat(max_lu.replace("Z", "+00:00")) if max_lu else None
    return feats, anchor


def load_current_features(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT raw FROM raw_ndw_dotnl")
        return [row[0] for row in cur.fetchall()]


def main():
    conn = get_connection()
    ensure_tables(conn)

    cache_feats, anchor = load_cache_features()
    print(f"Cache: {len(cache_feats)} historical stations, anchor {anchor.date() if anchor else '?'}")
    n_hist = upsert_registry(conn, cache_feats, now=anchor, first_seen_override=anchor)
    print(f"  seeded {n_hist} backdated rows")

    now = datetime.now(timezone.utc)
    current = load_current_features(conn)
    n_now = upsert_registry(conn, current, now=now)
    print(f"Current: {n_now} national stations upserted at {now.date()}")

    counts = detect_churn(conn, now, grace_hours=24)
    print(f"Churn detected: {counts}")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM dotnl_station_registry")
        reg = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM dotnl_idchurn_events")
        ev = cur.fetchone()[0]
    print(f"Registry now holds {reg} stations; {ev} churn events logged.")
    conn.close()


if __name__ == "__main__":
    main()

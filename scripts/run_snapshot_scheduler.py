"""Long-running loop: take a DOT-NL occupancy snapshot every SNAPSHOT_INTERVAL_MINUTES
(default 15). Run this in the background so dotnl_occupancy_snapshots
accumulates real occupancy history while the rest of NEDSI is built --
that history is what a real forecast model would eventually train on,
replacing the dashboard's current simulated peak-occupancy model.

One failed snapshot (e.g. a transient NDW API error) is logged and skipped,
not fatal -- the loop keeps running so a single blip doesn't kill hours of
accumulated scheduling.
"""

import os
import time
import traceback
from datetime import datetime, timezone

from snapshot_dotnl_occupancy import take_snapshot

INTERVAL_SECONDS = int(os.environ.get("SNAPSHOT_INTERVAL_MINUTES", "15")) * 60


def main():
    print(f"DOT-NL snapshot scheduler started. Interval: {INTERVAL_SECONDS // 60} minutes.", flush=True)
    while True:
        try:
            take_snapshot()
        except Exception:
            print(f"[{datetime.now(timezone.utc).isoformat()}] Snapshot failed, will retry next interval:", flush=True)
            traceback.print_exc()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

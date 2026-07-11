"""Snap every golden_stations row to its nearest NWB road segment (wegvak).

Uses nwb.get_nearest_wegvak() -- the same function used interactively --
reusing one connection across all rows instead of reconnecting per lookup.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nwb import get_connection, get_nearest_wegvak  # noqa: E402


def main():
    conn = get_connection()

    with conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE golden_stations "
            "ADD COLUMN IF NOT EXISTS wegvak_id BIGINT, "
            "ADD COLUMN IF NOT EXISTS wegvak_distance_m REAL;"
        )
    conn.commit()
    print("Added wegvak_id / wegvak_distance_m columns (if not already present).")

    with conn.cursor() as cur:
        cur.execute("SELECT id, ST_Y(geom), ST_X(geom) FROM golden_stations ORDER BY id;")
        rows = cur.fetchall()
    print(f"Snapping {len(rows)} golden stations to the nearest wegvak...")

    with conn.cursor() as update_cur:
        for i, (station_id, lat, lon) in enumerate(rows):
            nearest = get_nearest_wegvak(lat, lon, conn=conn)
            wvk_id = nearest["wvk_id"] if nearest else None
            distance_m = nearest["distance_m"] if nearest else None
            update_cur.execute(
                "UPDATE golden_stations SET wegvak_id = %s, wegvak_distance_m = %s WHERE id = %s",
                (wvk_id, distance_m, station_id),
            )
            if (i + 1) % 10_000 == 0:
                conn.commit()
                print(f"  {i + 1}/{len(rows)} snapped...")
    conn.commit()
    print("Done.")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), COUNT(wegvak_id), ROUND(AVG(wegvak_distance_m)::numeric, 1), "
            "ROUND(MAX(wegvak_distance_m)::numeric, 1) FROM golden_stations;"
        )
        total, snapped, avg_dist, max_dist = cur.fetchone()
        print(f"Snapped {snapped}/{total} stations. avg distance: {avg_dist} m, max: {max_dist} m")

    conn.close()


if __name__ == "__main__":
    main()

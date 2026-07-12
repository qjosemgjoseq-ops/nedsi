# Issues & gotchas log

A running log of bugs, environment quirks, and upstream data/API surprises hit while building NEDSI, and how they were resolved. Newest first.

---

## 2026-07-12 — Grid station open data: Stedin's data is far sparser than advertised, Liander WFS has a broken pagination parameter, Enexis has no direct-download option

**Symptom/context:** Ingesting real electricity substation locations (Liander + Stedin open data) for a new `grid_stations` map layer, to show "which physical station serves this area" instead of just an abstract congestion color.

**Finding 1 (Stedin data quality):** Stedin's "station" shapefiles (`hoogspanningsstations.zip`, `middenspanningsstations.zip`) are building-footprint POLYGONS, not point locations, and the only attribute field (`Hoogspa_ID` / `Middens_ID`) is empty in **100% of records** (checked all 34,569 middenspanning + 1,104 hoogspanning rows). Stedin's own page footnotes this ("Wat voor soort component het betreft wordt niet getoond"), but doesn't make clear the ID field is *entirely* unpopulated, not just sometimes. We use each polygon's centroid as the point location; popups show only "Stedin" with no name/voltage, unlike Liander's data.

**Finding 2 (Liander WFS bug):** Liander's public WFS (`dservices1.arcgis.com/.../Liander_Open_Data_Elektra_WFS/WFSServer`) advertises standard WFS 2.0 `startIndex` pagination, but any `startIndex > 0` silently returns zero features regardless of the real dataset size (58,328 middenspanning stations exist; only the first 3,000-feature page was ever reachable via startIndex). Worked around by tiling the Netherlands into a `BBOX`-filtered grid instead (same recursive-split-when-saturated pattern as `ingest_ndw_dotnl.py`), which does work correctly and retrieves the full dataset.

**Finding 3 (Enexis):** Enexis has no direct-download open dataset for station locations at all (as of today) — their "Open Data" page routes every dataset request through the shared "Partners in Energie" portal, with a stated 5-business-day turnaround and possible costs. Not included in `grid_stations`.

**Fix:** `scripts/ingest_grid_stations.py` ingests Liander (bbox-tiled WFS, rich data: name + voltage level) and Stedin (shapefile, centroid-only, no name) into one `grid_stations` table (94,905 rows total). Enexis skipped.

**Files:** `scripts/ingest_grid_stations.py`, `webapp/app/backend/routers/grid_stations.py`

**Impact:** Anyone building on Stedin's "station" open data should not expect per-station identifiers or names -- only anonymous point locations. Anyone paginating Liander's WFS beyond the first page needs to use BBOX tiling, not `startIndex`.

---

## 2026-07-11 — Congestie Kaart Zuid-Holland still shows slight cross-province overlap (deferred)

**Symptom:** After switching province-scope filtering from bbox rectangles to real CBS polygon `ST_Contains`/`ST_Intersects` (see the province-bbox-leakage fix below), the user reports the Zuid-Holland "Congestie Kaart" section still shows a small amount of overlap into a neighboring province. All other provinces (verified: Utrecht) looked clean after the fix.

**Likely cause:** `congestion_areas` (capaciteitskaart voedingsgebieden) are real polygons that can legitimately straddle a province border — `/api/v1/congestion/areas?provincie=...` uses `ST_Intersects` (not `ST_Contains`) against the real province polygon, so any voedingsgebied that merely touches Zuid-Holland renders in full, including the part that extends into the neighboring province. That may be entirely correct/expected behavior (real data, not a bug) rather than leftover leakage — needs to be distinguished from an actual polygon/matching bug before deciding on a fix (e.g. clipping the polygon at the province border for display, vs. just visually showing the whole real voedingsgebied).

**Status:** Deferred — user explicitly said "not a big issue for now," flagged as a reminder to revisit after the rest of NEDSI is built.

**Files:** `webapp/app/backend/routers/congestion.py` (`AREAS_BY_PROVINCE_SQL`)

---

## 2026-07-11 — NDW DOT-NL `open` field is false for 99.7% of stations (reportable to NDW)

**Symptom:** Attempted to use `properties.open === false` as a "closed/out of order" indicator for the dashboard's Buiten Gebruik tile; suddenly 100% of Utrecht stations showed as closed.

**Root cause:** Checked the national distribution in `raw_ndw_dotnl`: 66,203 of 66,370 stations (99.7%) report `open: false`, including stations that are demonstrably operational with live availability updates. The field is mis-populated at the source (CPOs likely not supplying opening-hours data, with `false` as the default) and carries no usable signal. Consequence: **DOT-NL has no working out-of-order/defect indicator at all** — a station with 0/2 available is indistinguishable from a defect one.

**Fix:** Reverted the `open` check; relabeled the tile honestly ("Geen statusdata" = stations without availability data, the only thing DOT-NL actually lets us detect). Added the finding to [NDW_DATA_QUALITY_REPORT.md](NDW_DATA_QUALITY_REPORT.md) — worth reporting since a broken `open` field affects every DOT-NL consumer.

**Files:** `webapp/app/frontend/src/hooks/useChargingData.ts`, `NDW_DATA_QUALITY_REPORT.md`

**Impact:** The dashboard cannot show true defect status until NDW/CPOs fix the field (or LINDA access provides it). Anyone analyzing DOT-NL "open" data gets garbage.

---

## 2026-07-11 — Same network TLS issue, third HTTP client (webapp backend)

**Symptom:** `C:\nedis\webapp\app\backend`'s new OpenChargeMap proxy endpoint failed with `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate`, calling `httpx` this time.

**Root cause:** Same as the earlier `requests`/Overpass issue and the `curl`/Rijkswaterstaat issue — this network's proxy/inspection CA isn't in `certifi`'s bundle but is trusted by Windows. Third distinct HTTP client hitting the identical root cause (`curl`'s schannel, Python `requests`, now Python `httpx`), confirming this is a machine/network-level condition, not a per-library quirk.

**Fix:** Same fix as before — installed `truststore` in the webapp backend's venv and called `truststore.inject_into_ssl()` early in `main.py` (before any `httpx` calls happen), redirecting TLS verification to the Windows certificate store.

**Files:** `webapp/app/backend/main.py`, `webapp/app/backend/requirements.default`

**Impact:** Fixed the OCM proxy immediately. Worth remembering: **any new HTTP client added to either the data-platform scripts or the webapp backend on this machine will need the same `truststore` treatment** — it's not a one-off, it's a standing property of this dev environment.

---

## 2026-07-10 — station_congestion join took 16+ minutes and never finished

**Symptom:** A simple `GROUP BY` count query against the `station_congestion` view ran for 16+ minutes with no sign of finishing; had to be cancelled with `pg_cancel_backend`.

**Root cause:** Two compounding issues. First, the capaciteitskaart polygons from the ArcGIS FeatureServer are extremely high-resolution — 927 polygons with 16.8M total vertices (~9,000 vertices/polygon average) — so every `ST_Contains` point-in-polygon test, even with the GIST index narrowing candidates first, does real work against thousands of vertices. Second, `station_congestion` was a plain (non-materialized) `VIEW` with four separate `LEFT JOIN LATERAL` point-in-polygon checks (capaciteitskaart afname/invoeding, Liander afname/invoeding); Postgres re-evaluates all four for every row on every query, even when only one column is selected, since it can't prove the unused LATERAL branches have no side effects.

**Fix:** 1) Simplified all congestion polygons in place with `ST_SimplifyPreserveTopology(geom, 0.0002)` (~20m tolerance) — negligible precision loss for city/region-sized supply areas, but cut vertex counts 24x (capaciteitskaart) and 5.6x (Liander). 2) Converted `station_congestion` from a `VIEW` to a `MATERIALIZED VIEW`, so the expensive 4-way join runs once (~47s) instead of on every query.

**Files:** `scripts/ingest_congestion.py`

**Impact:** Full congestion-status breakdown query dropped from "still running after 16 minutes" to 0.05s. Trade-off: the materialized view is now a snapshot — it needs `REFRESH MATERIALIZED VIEW station_congestion` (or re-running the script) after `golden_stations` or `congestion_areas` changes, it won't auto-update. Also worth remembering for future geometry ingestion from ArcGIS FeatureServers: check vertex density before joining against it at scale, not after.

---

## 2026-07-10 — NDW mislabels ~22 foreign stations as Dutch (reportable to NDW)

**Symptom:** After fixing the bbox-margin issue below, 22 NDW-sourced golden stations are still >1km (up to 55.9km) from the nearest NWB wegvak.

**Root cause:** These features carry `properties.country == "NLD"` in NDW's own API response, so our country filter can't catch them. Checking the actual street addresses confirms this isn't a border edge case: 18 are real Antwerp, Belgium addresses ("Sint-Bavostraat", "Begijnenstraat", "Villerslei"...), and 2 are in the Niederrhein region of Germany, 50-56km from the Dutch border. Likely cause: the CPOs behind these stations (E-Flux by Road, Vattenfall InCharge, DAEN Mobility) are submitting non-Dutch locations into the DOT-NL feed under an `NL-` ID prefix with `country: NLD` hardcoded or defaulted rather than derived from the address.

**Fix:** None applied on our end — this is an upstream data-quality issue in NDW's source data, not something our ingestion can filter away (the API itself says these are Dutch). Documented as a full report with station IDs, addresses, and coordinates in [NDW_DATA_QUALITY_REPORT.md](NDW_DATA_QUALITY_REPORT.md), ready to send to NDW's data quality team so they can follow up with the CPOs.

**Files:** `scripts/ingest_ndw_dotnl.py`, `NDW_DATA_QUALITY_REPORT.md`

**Impact:** 22 / 83,249 golden records (0.03%) have an unreliable `wegvak_id` snap. Low priority; revisit only if station-to-road matching quality becomes a blocker for a specific municipality near the border.

---

## 2026-07-10 — OSM query and NDW bbox pulled in out-of-scope stations

**Symptom:** `wegvak_distance_m` after snapping to NWB showed a max of ~8,981km (!) and an average of 455m, wildly inconsistent with the median of 8m.

**Root cause:** Two separate issues:
1. The Overpass query used `area["ISO3166-1"="NL"][admin_level=2]`, which also matched the Kingdom-level OSM relation — pulling in 4 charging stations actually located in **Aruba**.
2. The NDW ingestion tiles a rectangular bounding box over the Netherlands; since NL's border isn't rectangular, the box's margin caught ~30 genuine stations just across the German/Belgian border.

**Fix:**
1. Added `[bbox:50.6,3.2,53.7,7.3]` to the Overpass query to hard-clip every statement to mainland NL, regardless of how the area filter behaves.
2. Filtered NDW features on the API's own `properties.country == "NLD"` field after fetching (the API already tells us the country — no need to compute or guess a boundary).

**Files:** `scripts/ingest_osm_overpass.py`, `scripts/ingest_ndw_dotnl.py`

**Impact:** Aruba outliers fully eliminated (0 stations >100km). Border outliers dropped from ~35 to 22 (see entry above for the remainder). Average snap distance dropped from 455m to 19.2m across 83,249 golden records.

---

## 2026-07-10 — `match_method` showed duplicated reason strings

**Symptom:** Some golden records had `match_method = "connectors+operator+connectors"` — a reason listed twice.

**Root cause:** `is_match()` returns a combined string like `"operator+connectors"` as a single value. The fusion code was adding these whole strings to a `set()` to dedupe, but `"operator+connectors"` and `"connectors"` are different strings even though they share an atomic reason — so the set didn't catch the overlap, and joining produced a repeat.

**Fix:** Split each method string on `"+"` before adding to the set, so atomic reasons (not method-string combinations) are what gets deduplicated.

**Files:** `fusion.py`

**Impact:** Cosmetic only — didn't affect matching decisions or confidence scores, just the readability of `match_method`. Caught by manually reconciling row counts after the first fusion run, not by the original test suite; added a regression test (`test_match_method_deduplicates_atomic_reasons_across_pairs`) to close that gap.

---

## 2026-07-10 — OpenChargeMap `offset` pagination silently ignored

**Symptom:** First ingestion run inserted 487,500 rows into `raw_openchargemap`, but only 500 distinct `ocm_id` values existed among them.

**Root cause:** The OCM API accepts an `offset` query parameter but doesn't actually apply it — every page request returned the same first 500 results, so the pagination loop looped forever until it happened to stop (page size never dropped below the limit).

**Fix:** Switched to `sortby=id_asc` + `greaterthanid=<last seen id>` pagination instead, which the API does respect. Added a `UNIQUE` constraint on `ocm_id` with `ON CONFLICT DO NOTHING` as a safety net against any future duplicate pages.

**Files:** `scripts/ingest_openchargemap.py`

**Impact:** True NL dataset size is 8,162 stations (was silently 60x inflated with duplicates before the fix). Found by spot-checking `COUNT(*) vs COUNT(DISTINCT ocm_id)` after the first run looked suspiciously slow/large.

---

## 2026-07-10 — Copy-paste bug: wrong placeholder count in NDW insert

**Symptom:** `psycopg.ProgrammingError: the query has 6 placeholders but 5 parameters were passed`, after a ~15-minute crawl of all 63 NDW tiles had already completed successfully.

**Root cause:** The `INSERT` SQL was copy-pasted from the OSM script (which has two ID columns: `osm_type`, `osm_id`) but NDW only has one (`ndw_id`), leaving one extra `%s` placeholder.

**Fix:** Removed the extra placeholder from the `VALUES` clause.

**Files:** `scripts/ingest_ndw_dotnl.py`

**Impact:** None beyond a wasted ~15-minute re-crawl (the tile-fetching itself was correct; only the final bulk insert failed). Worth remembering: validate INSERT statements against the actual column list, especially after copy-pasting between similar scripts.

---

## 2026-07-10 — NDW DOT-NL API caps results per request

**Symptom:** A small test bounding box (~0.2° × 0.15°, near Utrecht) returned exactly 1000 features — suspiciously round.

**Root cause:** The API enforces a max bounding-box area of 1.0 deg² *and* a hard cap of 1000 features per request, with no pagination mechanism. A naive single request (or even simple fixed-size tiling) would silently truncate dense urban areas.

**Fix:** Built an adaptive quad-tree crawler (`scripts/ingest_ndw_dotnl.py`): start with 0.5°×0.5° tiles, and recursively split any tile that comes back with ≥1000 features into 4 quadrants until it's under the cap (floor of 0.02° to bound worst-case recursion).

**Files:** `scripts/ingest_ndw_dotnl.py`

**Impact:** Full national coverage (66k+ features) instead of a truncated, undercounted dataset. This is also why the NDW ingestion script is more complex than the other two.

---

## 2026-07-10 — Overpass API rejects default User-Agent

**Symptom:** `curl` (and later `requests`) got `HTTP 406 Not Acceptable` from `overpass-api.de` with no explanatory body.

**Root cause:** The Overpass server rejects requests carrying a generic/default client User-Agent string.

**Fix:** Send a descriptive `User-Agent` header (`NEDSI/1.0 (EV charging data platform)`) on every request. Applied the same header proactively to the NDW client too, per the user's explicit heads-up.

**Files:** `scripts/ingest_osm_overpass.py`, `scripts/ingest_ndw_dotnl.py`, `scripts/ingest_openchargemap.py` (defensive)

**Impact:** None once fixed. Good reminder to always identify our client honestly to third-party APIs rather than relying on library defaults.

---

## 2026-07-10 — `requests` fails TLS verification that `curl` handles fine

**Symptom:** `requests.exceptions.SSLError: certificate verify failed: unable to get local issuer certificate` when calling `overpass-api.de`, even though `curl --ssl-no-revoke` to other hosts on the same machine worked.

**Root cause:** `requests`/`urllib3` verify TLS against the bundled `certifi` CA list, not the Windows certificate store. Something on this network (likely a corporate proxy or antivirus doing TLS inspection) installs its root CA into the Windows trust store but isn't in `certifi`'s bundle — so `curl` (via Windows' native `schannel`) trusts it, but Python's `ssl` module doesn't.

**Fix:** Added the `truststore` package and called `truststore.inject_into_ssl()` at the top of `scripts/retry.py` (imported by every ingestion script), which makes Python's `ssl` module use the OS certificate store instead of `certifi`.

**Files:** `scripts/retry.py`, `requirements.txt`

**Impact:** Fixes TLS verification for all three ingestion scripts uniformly, without disabling certificate verification (which would be insecure).

---

## 2026-07-10 — Windows `curl` fails TLS revocation check on Rijkswaterstaat's host

**Symptom:** `curl: (35) schannel: next InitializeSecurityContext failed: CRYPT_E_NO_REVOCATION_CHECK` when downloading the NWB shapefile from `downloads.rijkswaterstaatdata.nl`.

**Root cause:** Windows' native TLS stack (`schannel`) couldn't reach the certificate revocation server to check if the site's cert was revoked — a network-level restriction, not an actual certificate problem.

**Fix:** Added `--ssl-no-revoke` to bypass the revocation check (not certificate validation itself).

**Files:** none (ad hoc `curl` command; not part of a checked-in script)

**Impact:** Unblocked the initial NWB download. Same underlying class of issue as the `truststore` fix above, hit twice via two different HTTP clients.

---

## 2026-07-10 — NWB shapefile uses Latin-1 encoding, not UTF-8

**Symptom:** `shapefile.dbfFileException: Could not decode field name or text/memo field: b'Doekesl\xe2n...'` while loading `Wegvakken.shp`.

**Root cause:** Dutch government shapefiles (this one included) encode text fields in Latin-1/cp1252, not UTF-8. `b'\xe2'` is `â` in Latin-1 (as in the Frisian street name "Doekeslân"), which isn't valid UTF-8.

**Fix:** Open the shapefile reader with `encoding="latin1"` explicitly.

**Files:** `scripts/load_nwb.py`

**Impact:** None once fixed. Worth remembering for any future Dutch government shapefile.

---

## 2026-07-10 — No `shp2pgsql` or `ogr2ogr` available anywhere

**Symptom:** NDW's own loading scripts assume `shp2pgsql` is on the machine; it wasn't — not on the Windows host, and not inside the `postgis/postgis` Docker image either (nor `ogr2ogr`/GDAL).

**Root cause:** No GDAL toolchain installed, and the official PostGIS Docker image doesn't bundle the `shp2pgsql` CLI (only the SQL functions it wraps).

**Fix:** Wrote a pure-Python loader (`scripts/load_nwb.py`) using `pyshp` (no compiled dependencies) to read the shapefile and PostgreSQL's `COPY` protocol to bulk-load ~1.6M rows quickly, adapting NDW's target schema (SRID 28992) without needing their tooling.

**Files:** `scripts/load_nwb.py`, `requirements.txt`

**Impact:** Avoided installing a whole GDAL toolchain for one job; kept the project dependency-light and pure-Python.

---

## 2026-07-10 — `psycopg2-binary` fails to build on Python 3.14

**Symptom:** `pip install psycopg2-binary` tried to compile from source and failed with `pg_config executable not found` — Python 3.14 is too new to have prebuilt wheels for it yet.

**Root cause:** `psycopg2-binary` ships prebuilt wheels per Python version; none exist yet for 3.14 (released very recently relative to this project).

**Fix:** Switched to `psycopg[binary]` (psycopg v3), which is actively maintained and has 3.14 wheels.

**Files:** `requirements.txt`

**Impact:** Different import (`import psycopg` not `psycopg2`) and slightly different API, used consistently across the project from the start.

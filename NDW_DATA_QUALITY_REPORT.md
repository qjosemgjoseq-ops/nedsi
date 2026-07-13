# NDW DOT-NL data quality report

Prepared by NEDSI while building a fused Dutch charging-station dataset. Two findings.

## Finding 2 (2026-07-11): `open` field is false for 99.7% of all stations

Of 66,370 stations in a full national crawl of the DOT-NL GeoJSON API, **66,203 (99.7%) report `properties.open: false`** — including stations that are demonstrably operational and delivering live availability updates at the same moment. Only 167 stations nationally report `open: true`.

This makes the field unusable for any consumer: it cannot distinguish closed, defect, or restricted-access locations from normal public chargers. The likely cause is CPOs not supplying opening-hours/status data, with `false` applied as a default somewhere in the chain. Suggested question for the CPO onboarding process: should `open` be nullable/omitted when unknown, rather than defaulting to `false`?

Combined with the absence of any per-connector operational-status field (availabilities carry only `available`/`total` counts), DOT-NL currently offers **no way to detect defect or out-of-service charging points** — a station showing 0 of 2 available is indistinguishable from a broken one.

## Finding 1 (2026-07-10): 22 stations mistagged as country=NLD

These 22 records from the NDW DOT-NL charge-point-data API (`https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/charge-point-data/v1/features`) have `properties.country == "NLD"`, but their coordinates and street addresses place them well outside the Netherlands — mostly in the Antwerp region of Belgium, and two in the Niederrhein region of Germany. Distances below are to the nearest Dutch national road segment (NWB), used here only as a sanity-check signal, not a precise "how far into which country" measurement. These 22 records from the NDW DOT-NL charge-point-data API (`https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/charge-point-data/v1/features`) have `properties.country == "NLD"`, but their coordinates and street addresses place them well outside the Netherlands — mostly in the Antwerp region of Belgium, and two in the Niederrhein region of Germany. Distances below are to the nearest Dutch national road segment (NWB), used here only as a sanity-check signal, not a precise "how far into which country" measurement.

## Likely Belgium (Antwerp region) — 18 stations

| NDW ID | Address | Lat, Lon | CPO | Operator | Distance to nearest NL road |
|---|---|---|---|---|---|
| `NL-EFL-67cf1428253ffbd9733807f0` | Verbindingsstraat 2 | 50.90855, 3.32092 | EFL | E-Flux by Road | 38.1 km |
| `NL-EFL-68ad62bdc9eb8bd392e366f4` | Itegembaan 166 | 51.11018, 4.67111 | EFL | E-Flux by Road | 32.8 km |
| `NL-EFL-64135a42255c6800128b2707` | Kortrijksesteenweg 216 | 50.97173, 3.51918 | EFL | E-Flux by Road | 30.3 km |
| `NL-EFL-65e96aad884de4001c0df55f` | Startelstraat 92 | 50.84948, 5.26533 | EFL | E-Flux by Road | 26.3 km |
| `NL-DAE-923476` | Sint-Bavostraat 66 | 51.17217, 4.40723 | DAE | DAEN Mobility | 20.3 km |
| `NL-DAE-923477` | Sint-Bavostraat 66 | 51.17217, 4.40723 | DAE | DAEN Mobility | 20.3 km |
| `NL-EFL-6405be9a8b9d4e0012971d3e` | Fortbaan 72D | 51.19914, 4.49835 | EFL | E-Flux by Road | 18.9 km |
| `NL-NUO-21267250-c9d3-4df4-9892-f22a35ac8647` | Jachthoornlaan 1 | 51.26666, 4.70357 | NUO | Vattenfall InCharge | 17.1 km |
| `NL-EFL-611cd6653b3989b81373a5bc` | St.-Lambertusstraat 4 | 51.16386, 5.06706 | EFL | E-Flux by Road | 16.1 km |
| `NL-EFL-643d4934e1f96b0012855b81` | Houtum 53 | 51.22895, 4.97885 | EFL | E-Flux by Road | 15.9 km |
| `NL-NUO-b4a02745-914e-43c5-a11d-39a65d9049df` | Begijnenstraat 2 | 51.21283, 4.40119 | NUO | Vattenfall InCharge | 15.8 km |
| `NL-EFL-66ceeef7d02398001c3fb5ae` | Sint-Michielsestraat 23 | 51.17718, 3.23179 | EFL | E-Flux by Road | 15.1 km |
| `NL-EFL-63a1d115eb74e500126bd636` | Hoogstraat 77 | 51.11111, 4.08821 | EFL | E-Flux by Road | 14.8 km |
| `NL-EFL-635be1f58c3fdd16f302810f` | Villerslei 109 | 51.25221, 4.51466 | EFL | E-Flux by Road | 13.7 km |
| `NL-DAE-923486` | Esmoreitlaan 3 | 51.23286, 4.38481 | DAE | DAEN Mobility | 13.5 km |
| `NL-DAE-923553` | Esmoreitlaan 3 | 51.23286, 4.38481 | DAE | DAEN Mobility | 13.5 km |
| `NL-EFL-6662ca4a3bc735001c80e05e` | Roggeveld 18A | 51.14540, 4.06732 | EFL | E-Flux by Road | 10.8 km |
| `NL-EFL-69ce91f77444f93131c38938` | De Linde 91 | 51.21005, 3.28057 | EFL | E-Flux by Road | 10.1 km |

Two pairs share identical coordinates and address but different IDs (`NL-DAE-923476`/`923477` at Sint-Bavostraat 66; `NL-DAE-923486`/`923553` at Esmoreitlaan 3) — likely two connectors at the same physical location, both mistagged.

## Likely Germany (Niederrhein region) — 2 stations

| NDW ID | Address | Lat, Lon | CPO | Operator | Distance to nearest NL road |
|---|---|---|---|---|---|
| `NL-EFL-65f170a40b2b51001c8cf0bb` | Herckenstein 163 | 51.11232, 6.97058 | EFL | E-Flux by Road | 55.9 km |
| `NL-NUO-2a025333-08b1-457e-9392-ece500f4e962` | Amundsenweg 39 | 51.43091, 6.93586 | NUO | Vattenfall InCharge | 49.5 km |

## Lower-confidence / closer to the border — 2 stations

These are close enough to the Dutch border that the misclassification is more plausible as a genuine edge case rather than a clear-cut foreign address; included for completeness but worth double-checking before reporting.

| NDW ID | Address | Lat, Lon | CPO | Operator | Distance to nearest NL road |
|---|---|---|---|---|---|
| `NL-EFL-669e3950aadd76001ce11f8a` | Oostkerkestraat 40 | 51.30235, 3.29912 | EFL | E-Flux by Road | 4.4 km |
| `NL-DAE-923408` | Bijltje 4 | 51.44270, 4.73393 | DAE | DAEN Mobility | 3.1 km |

## How this was found

While building a fused "golden record" charging-station dataset (matching NDW against OpenChargeMap and OpenStreetMap, then snapping each station to the nearest Dutch NWB road segment), 22 NDW-sourced stations snapped absurdly far from any Dutch road despite carrying `country: "NLD"`. Cross-checking their addresses against the coordinates confirmed they're real streets — just not in the Netherlands. Full investigation notes: [ISSUES.md](ISSUES.md).

## Suggested report to NDW

The three CPOs involved (`EFL` / E-Flux by Road, `NUO` / Vattenfall InCharge, `DAE` / DAEN Mobility) appear to be submitting non-Dutch locations under the `NL-` ID prefix with `country: NLD`. Worth asking NDW's data quality team to check with these CPOs whether their DOT-NL feed submission is scoped correctly to NL-only locations, or whether the `country` field is being set incorrectly/defaulted rather than derived from the actual address.

---

## Register party-ID vs delivery party-ID mismatch (onboarding gap analysis)

**Found:** 2026-07-13, while building the CPO onboarding-gap report (registered
NL CPOs in the Benelux ID-register vs. parties actually delivering to DOT-NL).

Many brands do **not** deliver to DOT-NL under their own registered OCPI
party-ID. They deliver via their CPMS/platform provider's party-ID, so the
brand's register ID shows zero delivery even though their stations are present.
The `operator_name` field is the only bridge between the two.

Confirmed examples:

| Register brand (party-ID) | Actually delivered under | operator_name in feed | Stations |
|---|---|---|---|
| TotalEnergies Charging Services (`NL-TCB` / `NL-TOT`) | `NL-GFX` (GreenFlux) | TotalEnergies | 8 982 |
| EQUANS Infra & Mobility (`NL-VLN`) | `NL-LMS` (LastMileSolutions) | EQUANS | 11 652 |
| Opcharge (`NL-OPC`) | `NL-SGM` | Opcharge | 393 |
| Pluq Assets (`NL-PLQ`) | `NL-EFL` (E-Flux by Road) | Pluq | 386 |

Note `NL-SGM` is registered to "NRG Accounting" but carries Opcharge + Mick-E
stations; `NL-EFL` (E-Flux) carries E-Flux + Pluq + Heijmans. So one platform
party-ID aggregates multiple brands.

**Impact:** any coverage/onboarding analysis that matches the register party-ID
directly against the DOT-NL id prefix will **over-report the gap** — it flags
brands as "not connected" that are fully present under a platform ID. It also
means DOT-NL cannot attribute a station to its true operating brand from the
party-ID alone; the `operator_name` free-text field is load-bearing and
un-validated.

**Suggested to NDW:** consider capturing the operating brand as a structured,
validated field (not just free-text `operator_name`), and/or maintain a
party-ID → operating-brand mapping, so coverage and data-quality can be
measured per real operator rather than per submitting platform.

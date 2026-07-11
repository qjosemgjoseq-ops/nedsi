# NEDSI

## About the user
I'm a beginner. Always explain what you do in one short sentence per step. Always ask before deleting anything.

## What this is
NEDSI is a Dutch EV-charging-station data platform for municipalities and grid operators.

## Stack
- Python
- PostgreSQL + PostGIS, running in Docker
- Later: a Node/React dashboard

## Key data sources
- **NDW DOT-NL API** — live charging status; coordinates are unreliable
- **OpenChargeMap** — good coordinates; API key available
- **OpenStreetMap**
- **Dutch NWB road network**
- **Grid congestion maps** — Liander, Stedin, Netbeheer Nederland

## Core concept: the "golden record"
One fused, trusted record per charging station, matched from multiple sources and snapped to NWB road segments.

## Decision: road-segment snapping approach
We snap each golden station to its nearest NWB wegvak with a plain PostGIS KNN query (`nwb.get_nearest_wegvak`, using the `<->` operator against the `wegvakken` table), not with NDW's own [nls-routing-map-matcher](https://github.com/ndwnu/nls-routing-map-matcher).

**Why:** that tool solves a different problem — it's a Java library for matching a driven *trajectory* (a LineString/sequence of GPS points) onto a road network via GraphHopper route search, e.g. for matching a vehicle's path. Our need is the much simpler "nearest segment to one static point" per charging station. The library also ships no Docker image or standalone service — it's meant to be embedded into a Spring Boot app via Maven, with custom `Link`/`LinkVehicleMapper` classes you write yourself. Standing that up would mean building a Java service around a library designed for a different job, just to get single-point snapping we already have in three lines of SQL.

**How to apply:** keep using `get_nearest_wegvak` / KNN snapping for point-to-segment matching. Revisit this decision only if NEDSI later needs to map-match actual GPS *trajectories* (e.g. a vehicle route or a congestion trace) onto the road network — that's the problem nls-routing-map-matcher actually solves, and worth the Java integration cost at that point.

**Known caveat:** snapping distance is small for the vast majority of stations (median 8m, 95th percentile 43m across 83,249 golden records). A small number of NDW stations near the German/Belgian border still snap poorly because NDW's own source data mistags them as Dutch — see [ISSUES.md](ISSUES.md) for the full history and why this one wasn't chased further.

## Bug/gotcha log
Whenever you hit a non-obvious bug, environment quirk, or upstream API/data surprise while working on this project, log it in [ISSUES.md](ISSUES.md) (newest entry first) — symptom, root cause, fix, files touched, impact. Check it before re-debugging something that smells familiar (e.g. TLS errors, API pagination, encoding issues).

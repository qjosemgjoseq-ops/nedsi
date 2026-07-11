"""Station-matching logic for the NEDSI golden record fusion layer.

A record is a plain dict:
    {source, source_id, lon, lat, operator, connector_count}

Matching rule: two records are the same station iff they are less than
MATCH_DISTANCE_M apart AND share evidence (similar operator names, or equal
positive connector counts). Records from the same source never merge.

To survive charging plazas (many stations within 50 m of each other), accepted
candidate pairs are processed nearest-first and each record may pair with at
most one record per other source; clusters are then built with union-find.
"""

import math
import re
from difflib import SequenceMatcher

MATCH_DISTANCE_M = 50.0

# Coordinate trust order: OCM and OSM have good coordinates, NDW can be off
# by tens of meters.
COORD_PRIORITY = ["ocm", "osm", "ndw"]

_LEGAL_SUFFIXES = re.compile(r"\b(b\.?v\.?|n\.?v\.?|gmbh|ltd|inc|holding)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_operator(name):
    if not name:
        return ""
    name = name.lower()
    name = _NON_ALNUM.sub(" ", name)
    name = _LEGAL_SUFFIXES.sub(" ", name)
    return " ".join(name.split())


def operators_similar(a, b):
    """True if two operator names plausibly refer to the same company."""
    na, nb = normalize_operator(a), normalize_operator(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.8


def distance_m(a, b):
    """Approximate ground distance in meters between two records (equirectangular)."""
    mean_lat = math.radians((a["lat"] + b["lat"]) / 2)
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(mean_lat)
    dy = (a["lat"] - b["lat"]) * 111_320
    return math.hypot(dx, dy)


def is_match(a, b):
    """Return (matched, method) for a pair of records from different sources."""
    if distance_m(a, b) >= MATCH_DISTANCE_M:
        return False, None

    op_match = operators_similar(a.get("operator"), b.get("operator"))
    ca, cb = a.get("connector_count"), b.get("connector_count")
    conn_match = bool(ca) and bool(cb) and ca == cb

    if op_match and conn_match:
        return True, "operator+connectors"
    if op_match:
        return True, "operator"
    if conn_match:
        return True, "connectors"
    return False, None


def _confidence(n_sources, methods):
    """Score a golden record: more corroborating sources and stronger evidence rank higher."""
    if n_sources >= 3:
        base = 0.95
    elif n_sources == 2:
        base = 0.80
    else:
        return 0.50
    if methods == {"connectors"}:
        base -= 0.10  # connector-count-only evidence is weaker than an operator match
    return base


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        self.parent[self.find(i)] = self.find(j)


def fuse(records):
    """Cluster records from multiple sources into golden station records.

    Candidate pairs are evaluated nearest-first, and each record may pair with
    at most one record per other source, so dense plazas pair up point-by-point
    instead of chain-merging into one blob.
    """
    n = len(records)

    # Bucket records into a ~50 m grid so we only compare neighbors instead of
    # all n^2 pairs (the full dataset has ~100k records).
    cell_lat = MATCH_DISTANCE_M / 111_320
    cell_lon = MATCH_DISTANCE_M / (111_320 * math.cos(math.radians(52.2)))
    grid = {}
    for i, r in enumerate(records):
        key = (int(r["lat"] / cell_lat), int(r["lon"] / cell_lon))
        grid.setdefault(key, []).append(i)

    # Collect accepted cross-source pairs (checking the 3x3 neighborhood).
    pairs = []
    seen = set()
    for (gy, gx), indexes in grid.items():
        neighborhood = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                neighborhood.extend(grid.get((gy + dy, gx + dx), []))
        for i in indexes:
            for j in neighborhood:
                if i >= j or (i, j) in seen:
                    continue
                seen.add((i, j))
                if records[i]["source"] == records[j]["source"]:
                    continue
                matched, method = is_match(records[i], records[j])
                if matched:
                    pairs.append((distance_m(records[i], records[j]), i, j, method))
    pairs.sort(key=lambda p: p[0])

    # Greedy nearest-first: one partner per (record, other source).
    uf = _UnionFind(n)
    taken = set()  # (record index, other source) already paired
    pair_methods = {}
    for dist, i, j, method in pairs:
        key_i = (i, records[j]["source"])
        key_j = (j, records[i]["source"])
        if key_i in taken or key_j in taken:
            continue
        taken.add(key_i)
        taken.add(key_j)
        uf.union(i, j)
        pair_methods.setdefault(uf.find(i), set())
    # Record methods per final cluster (roots may have changed after unions).
    # Methods are split into their atomic parts so e.g. "operator+connectors"
    # from one pair and "connectors" from another dedup instead of concatenating.
    for dist, i, j, method in pairs:
        if uf.find(i) == uf.find(j):
            key_i = (i, records[j]["source"])
            if key_i in taken:
                pair_methods.setdefault(uf.find(i), set()).update(method.split("+"))

    # Build golden records per cluster.
    clusters = {}
    for i in range(n):
        clusters.setdefault(uf.find(i), []).append(records[i])

    golden = []
    for root, members in clusters.items():
        by_source = {}
        for m in members:
            by_source.setdefault(m["source"], []).append(m["source_id"])

        best = min(members, key=lambda m: COORD_PRIORITY.index(m["source"]))
        methods = pair_methods.get(root, set())
        n_sources = len(by_source)

        operator = next(
            (m["operator"] for m in sorted(members, key=lambda m: COORD_PRIORITY.index(m["source"])) if m.get("operator")),
            None,
        )

        golden.append(
            {
                "lon": best["lon"],
                "lat": best["lat"],
                "coord_source": best["source"],
                "operator": operator,
                "members": by_source,
                "match_method": "+".join(sorted(methods)) if methods else "single_source",
                "confidence": _confidence(n_sources, methods),
                "n_sources": n_sources,
            }
        )
    return golden

"""Tests for the station-matching logic in fusion.py — written before the implementation.

A record is a dict: {source, source_id, lon, lat, operator, connector_count}.
Matching rule: same station iff distance < 50 m AND (operators similar OR
connector counts match). Records from the same source never merge.
"""

import pytest

from fusion import fuse, is_match, operators_similar

# ~0.00045 degrees latitude is ~50 m; at NL latitude ~0.00073 degrees longitude is ~50 m.
# Base point: somewhere in Utrecht.
LAT, LON = 52.09, 5.12


def rec(source, source_id, lon=LON, lat=LAT, operator=None, connector_count=None):
    return {
        "source": source,
        "source_id": source_id,
        "lon": lon,
        "lat": lat,
        "operator": operator,
        "connector_count": connector_count,
    }


# ---------- operators_similar ----------


def test_identical_operators_are_similar():
    assert operators_similar("Allego", "Allego")


def test_case_and_punctuation_are_ignored():
    assert operators_similar("Shell Recharge", "shell-recharge")


def test_legal_suffixes_are_ignored():
    assert operators_similar("Allego B.V.", "Allego")


def test_containment_counts_as_similar():
    assert operators_similar("Vattenfall", "Vattenfall InCharge")


def test_unrelated_operators_are_not_similar():
    assert not operators_similar("Allego", "Shell Recharge")


def test_missing_operator_is_never_similar():
    assert not operators_similar(None, "Allego")
    assert not operators_similar("Allego", None)
    assert not operators_similar(None, None)


# ---------- is_match ----------


def test_close_with_same_operator_matches():
    a = rec("ocm", 1, operator="Allego")
    b = rec("ndw", "NL-X", lon=LON + 0.0002, operator="Allego")  # ~14 m away
    matched, method = is_match(a, b)
    assert matched
    assert method == "operator"


def test_close_with_matching_connector_counts_matches():
    a = rec("ocm", 1, connector_count=4)
    b = rec("ndw", "NL-X", lon=LON + 0.0002, connector_count=4)
    matched, method = is_match(a, b)
    assert matched
    assert method == "connectors"


def test_close_with_both_criteria_reports_both():
    a = rec("ocm", 1, operator="Allego", connector_count=2)
    b = rec("ndw", "NL-X", lon=LON + 0.0002, operator="allego", connector_count=2)
    matched, method = is_match(a, b)
    assert matched
    assert method == "operator+connectors"


def test_close_but_no_shared_evidence_does_not_match():
    a = rec("ocm", 1, operator="Allego", connector_count=4)
    b = rec("ndw", "NL-X", lon=LON + 0.0002, operator="Shell Recharge", connector_count=2)
    matched, _ = is_match(a, b)
    assert not matched


def test_far_apart_never_matches_even_with_same_operator():
    a = rec("ocm", 1, operator="Allego")
    b = rec("ndw", "NL-X", lon=LON + 0.01, operator="Allego")  # ~680 m away
    matched, _ = is_match(a, b)
    assert not matched


def test_missing_connector_counts_do_not_count_as_matching():
    a = rec("ocm", 1, connector_count=None)
    b = rec("ndw", "NL-X", lon=LON + 0.0002, connector_count=None)
    matched, _ = is_match(a, b)
    assert not matched


def test_zero_connector_counts_do_not_count_as_matching():
    a = rec("ocm", 1, connector_count=0)
    b = rec("ndw", "NL-X", lon=LON + 0.0002, connector_count=0)
    matched, _ = is_match(a, b)
    assert not matched


# ---------- fuse: basic clustering ----------


def test_single_source_station_becomes_its_own_golden_record():
    golden = fuse([rec("osm", 42, operator="Fastned")])
    assert len(golden) == 1
    g = golden[0]
    assert g["match_method"] == "single_source"
    assert g["members"] == {"osm": [42]}


def test_two_source_match_produces_one_golden_record():
    a = rec("ocm", 1, operator="Fastned")
    b = rec("ndw", "NL-F1", lon=LON + 0.0002, operator="Fastned")
    golden = fuse([a, b])
    assert len(golden) == 1
    g = golden[0]
    assert g["members"] == {"ocm": [1], "ndw": ["NL-F1"]}


def test_three_source_chain_merges_into_one_record():
    a = rec("ocm", 1, operator="Fastned")
    b = rec("osm", 2, lon=LON + 0.0001, operator="Fastned")
    c = rec("ndw", "NL-F1", lon=LON + 0.0002, operator="Fastned")
    golden = fuse([a, b, c])
    assert len(golden) == 1
    assert set(golden[0]["members"]) == {"ocm", "osm", "ndw"}


def test_same_source_records_never_merge():
    a = rec("ocm", 1, operator="Allego")
    b = rec("ocm", 2, lon=LON + 0.0001, operator="Allego")
    golden = fuse([a, b])
    assert len(golden) == 2


def test_golden_coordinates_prefer_ocm_then_osm_over_ndw():
    ocm = rec("ocm", 1, lon=5.1200, operator="Allego")
    ndw = rec("ndw", "NL-A1", lon=5.1202, operator="Allego")  # NDW coords are untrusted
    golden = fuse([ocm, ndw])
    assert golden[0]["lon"] == 5.1200

    osm = rec("osm", 7, lon=5.3300, lat=52.2, operator="Allego")
    ndw2 = rec("ndw", "NL-A2", lon=5.3302, lat=52.2, operator="Allego")
    golden = fuse([osm, ndw2])
    assert golden[0]["lon"] == 5.3300


def test_confidence_ranks_more_sources_higher():
    three = fuse(
        [
            rec("ocm", 1, operator="Fastned"),
            rec("osm", 2, lon=LON + 0.0001, operator="Fastned"),
            rec("ndw", "N1", lon=LON + 0.0002, operator="Fastned"),
        ]
    )[0]
    two = fuse(
        [
            rec("ocm", 1, operator="Fastned"),
            rec("ndw", "N1", lon=LON + 0.0002, operator="Fastned"),
        ]
    )[0]
    one = fuse([rec("ocm", 1, operator="Fastned")])[0]
    assert three["confidence"] > two["confidence"] > one["confidence"]


# ---------- fuse: plaza edge case ----------


def test_plaza_pairs_nearest_points_instead_of_chain_merging():
    """A charging plaza: two Allego stations 30 m apart, each present in OCM and NDW.

    All four points are within 50 m of each other with the same operator, so a
    naive matcher would chain-merge everything into one blob. The matcher must
    instead pair each OCM point with its *nearest* NDW counterpart, giving two
    golden records with two sources each.
    """
    ocm_a = rec("ocm", 1, lon=5.12000, operator="Allego", connector_count=4)
    ocm_b = rec("ocm", 2, lon=5.12040, operator="Allego", connector_count=2)  # ~27 m east
    ndw_a = rec("ndw", "N-A", lon=5.12002, operator="Allego", connector_count=4)  # ~1.4 m from A
    ndw_b = rec("ndw", "N-B", lon=5.12042, operator="Allego", connector_count=2)  # ~1.4 m from B

    golden = fuse([ocm_a, ocm_b, ndw_a, ndw_b])

    assert len(golden) == 2
    by_ocm = {g["members"]["ocm"][0]: g for g in golden}
    assert by_ocm[1]["members"]["ndw"] == ["N-A"]
    assert by_ocm[2]["members"]["ndw"] == ["N-B"]


def test_plaza_with_different_operators_stays_separate():
    """Two different operators 10 m apart must not merge, even in a dense plaza."""
    a = rec("ocm", 1, operator="Shell Recharge", connector_count=4)
    b = rec("ndw", "N1", lon=LON + 0.00015, operator="Allego", connector_count=2)
    golden = fuse([a, b])
    assert len(golden) == 2


def test_match_method_deduplicates_atomic_reasons_across_pairs():
    """A golden record's match_method must not contain a reason twice, even when
    one pair contributed "operator+connectors" and another contributed "connectors"."""
    ocm = rec("ocm", 1, operator="Allego", connector_count=4)
    osm = rec("osm", 2, lon=LON + 0.0001, operator="Allego", connector_count=4)
    ndw = rec("ndw", "N1", lon=LON + 0.0002, operator=None, connector_count=4)
    golden = fuse([ocm, osm, ndw])
    assert len(golden) == 1
    reasons = golden[0]["match_method"].split("+")
    assert len(reasons) == len(set(reasons))

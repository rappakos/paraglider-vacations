"""Unit tests for the scoring layer (pure dicts, no DB)."""

import math

from app.services.scoring import FEATURE_REGISTRY, rank_regions


# --------------------------------------------------------------------------- #
# Feature registry / derivation
# --------------------------------------------------------------------------- #

def test_feature_derivation_ratio(make_row, region_meta):
    row = make_row("alpha", fai_triangle_count=10, flights_in_window=100)
    assert FEATURE_REGISTRY["fai_triangle_share"].derive(row, region_meta["alpha"]) == 0.1


def test_feature_derivation_zero_denominator_is_nan(make_row, region_meta):
    row = make_row("alpha", fai_triangle_count=0, flights_in_window=0)
    assert math.isnan(FEATURE_REGISTRY["fai_triangle_share"].derive(row, region_meta["alpha"]))


# --------------------------------------------------------------------------- #
# Min-Max
# --------------------------------------------------------------------------- #

def test_minmax_degenerate_feature_is_neutral(make_row, region_meta):
    rows = [make_row(k, median_max_altitude=2000.0) for k in ("alpha", "bravo", "charlie")]
    res = rank_regions(rows, {"max_altitude": 1.0}, region_meta, method="minmax")
    for r in res:
        assert r.features["max_altitude"].normalized_score == 0.5
        assert r.total_score == 0.5


def test_minmax_polarity_lower_traffic_wins(make_row, region_meta):
    rows = [
        make_row("alpha", flights_per_flyable_day=2.0),    # least busy -> best
        make_row("bravo", flights_per_flyable_day=10.0),
        make_row("charlie", flights_per_flyable_day=20.0),
    ]
    res = rank_regions(rows, {"low_traffic": 1.0}, region_meta, method="minmax")
    assert res[0].region_key == "alpha"
    assert res[0].features["low_traffic"].normalized_score == 1.0
    assert res[-1].region_key == "charlie"
    assert res[-1].features["low_traffic"].normalized_score == 0.0


def test_nan_region_loses_both_methods(make_row, region_meta):
    rows = [
        make_row("alpha", fai_triangle_count=20, flights_in_window=100),
        make_row("bravo", fai_triangle_count=5, flights_in_window=100),
        make_row("charlie", fai_triangle_count=0, flights_in_window=0),  # fai_triangle_share -> nan
    ]
    for method in ("minmax", "rrf"):
        res = rank_regions(rows, {"fai_triangle_share": 1.0}, region_meta, method=method)
        assert res[-1].region_key == "charlie", method
        assert res[-1].features["fai_triangle_share"].raw_value is None, method
        assert res[0].region_key == "alpha", method


# --------------------------------------------------------------------------- #
# RRF
# --------------------------------------------------------------------------- #

def test_rrf_ties_get_equal_score(make_row, region_meta):
    rows = [
        make_row("alpha", flights_per_flyable_day=5.0, flights_in_window=100),
        make_row("bravo", flights_per_flyable_day=5.0, flights_in_window=100),  # tie with alpha
        make_row("charlie", flights_per_flyable_day=99.0, flights_in_window=100),
    ]
    res = rank_regions(rows, {"low_traffic": 1.0}, region_meta, method="rrf")
    by_key = {r.region_key: r for r in res}
    assert by_key["alpha"].total_score == by_key["bravo"].total_score
    assert by_key["charlie"].total_score < by_key["alpha"].total_score


# --------------------------------------------------------------------------- #
# Weights / shape
# --------------------------------------------------------------------------- #

def test_zero_weight_and_unknown_key_excluded(make_row, region_meta):
    rows = [make_row(k) for k in ("alpha", "bravo", "charlie")]
    res = rank_regions(
        rows,
        {"fai_triangle_share": 0.8, "low_traffic": 0.0, "not_a_feature": 1.0},
        region_meta,
        method="minmax",
    )
    assert set(res[0].features.keys()) == {"fai_triangle_share"}


def test_no_active_features_orders_by_coverage(make_row, region_meta):
    rows = [
        make_row("alpha", flights_in_window=10),
        make_row("bravo", flights_in_window=500),
        make_row("charlie", flights_in_window=100),
    ]
    res = rank_regions(rows, {}, region_meta, method="minmax")
    assert [r.region_key for r in res] == ["bravo", "charlie", "alpha"]
    assert all(r.total_score == 0.0 for r in res)
    assert all(r.features == {} for r in res)


def test_response_shape_consistent_across_methods(make_row, region_meta):
    rows = [make_row(k) for k in ("alpha", "bravo", "charlie")]
    weights = {"fai_triangle_share": 0.8, "low_traffic": 0.6, "max_altitude": 0.3}
    mm = rank_regions(rows, weights, region_meta, method="minmax")
    rrf = rank_regions(rows, weights, region_meta, method="rrf")
    assert {r.region_key for r in mm} == {r.region_key for r in rrf}
    assert mm[0].features.keys() == rrf[0].features.keys()
    for res in (mm, rrf):
        assert [r.rank for r in res] == [1, 2, 3]
        for r in res:
            assert 0.0 <= r.total_score <= 1.0
            assert r.name  # name resolved from region_meta


def test_ranks_are_dense_and_sorted(make_row, region_meta):
    rows = [
        make_row("alpha", fai_triangle_count=30),
        make_row("bravo", fai_triangle_count=10),
        make_row("charlie", fai_triangle_count=20),
    ]
    res = rank_regions(rows, {"fai_triangle_share": 1.0}, region_meta, method="minmax")
    scores = [r.total_score for r in res]
    assert scores == sorted(scores, reverse=True)
    assert [r.rank for r in res] == [1, 2, 3]

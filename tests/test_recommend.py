"""End-to-end tests for POST /recommend (aggregation patched — no DB)."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.routes as routes
from app.main import app

client = TestClient(app)

# Three regions for ISO week 25; deterministic, mirrors aggregation output shape.
_FAKE = pd.DataFrame([
    {"region_key": "greifenburg", "flights_in_window": 1200, "flights_per_flyable_day": 23.0,
     "fai_triangle_count": 120, "en_a_count": 100, "median_max_altitude": 2500.0,
     "expected_weekly_airtime_h": 12.0, "p67_duration_sec": 7700.0, "years_covered": [2019, 2020, 2021]},
    {"region_key": "gemona", "flights_in_window": 40, "flights_per_flyable_day": 4.7,
     "fai_triangle_count": 8, "en_a_count": 6, "median_max_altitude": 1700.0,
     "expected_weekly_airtime_h": 4.7, "p67_duration_sec": 7900.0, "years_covered": [2019, 2021]},
    {"region_key": "tolmin", "flights_in_window": 500, "flights_per_flyable_day": 11.6,
     "fai_triangle_count": 23, "en_a_count": 50, "median_max_altitude": 1730.0,
     "expected_weekly_airtime_h": 11.6, "p67_duration_sec": 9778.0, "years_covered": [2019, 2020, 2021]},
])


@pytest.fixture(autouse=True)
def patch_aggregate(monkeypatch):
    monkeypatch.setattr(routes, "aggregate_for_date", lambda *a, **k: _FAKE.copy())


def test_recommend_minmax_ranked():
    r = client.post("/api/recommend", json={
        "date": "2026-06-15", "method": "minmax",
        "preferences": {"xc_style": {"weight": 0.8}, "short_drive": {"weight": 0.3}},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["calendar_week"] == 25 and body["method"] == "minmax"
    assert [x["rank"] for x in body["regions"]] == [1, 2, 3]
    scores = [x["total_score"] for x in body["regions"]]
    assert scores == sorted(scores, reverse=True)
    # data_coverage surfaced
    assert body["regions"][0]["data_coverage"]["flights_in_window"] > 0
    assert body["regions"][0]["data_coverage"]["years_covered"]


def test_recommend_rrf_same_shape():
    payload = {"date": "2026-06-15", "method": "rrf",
               "preferences": {"xc_style": {"weight": 0.8}, "short_drive": {"weight": 0.3}}}
    r = client.post("/api/recommend", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "rrf"
    assert [x["rank"] for x in body["regions"]] == [1, 2, 3]
    assert set(body["regions"][0]["features"].keys()) == {"xc_style", "short_drive"}


def test_recommend_get_default_profile():
    r = client.get("/api/recommend", params={"date": "2026-06-15"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "minmax"
    assert body["weights"] == {"airtime": 1.0}
    assert [x["rank"] for x in body["regions"]] == [1, 2, 3]
    scores = [x["total_score"] for x in body["regions"]]
    assert scores == sorted(scores, reverse=True)
    assert set(body["regions"][0]["features"].keys()) == {"airtime"}


def test_recommend_get_query_overrides():
    r = client.get("/api/recommend", params={"date": "2026-06-15", "method": "rrf",
                                             "xc_style": "1", "airtime": "0"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "rrf"
    assert body["weights"] == {"xc_style": 1.0, "airtime": 0.0}
    assert set(body["regions"][0]["features"].keys()) == {"xc_style"}


def test_recommend_validation_rejects_bad_weight():
    r = client.post("/api/recommend", json={
        "date": "2026-06-15", "preferences": {"xc_style": {"weight": 1.5}}})
    assert r.status_code == 422


def test_recommend_missing_date_is_422():
    r = client.post("/api/recommend", json={"preferences": {}})
    assert r.status_code == 422


def test_recommend_empty_preferences_orders_by_coverage():
    r = client.post("/api/recommend", json={"date": "2026-06-15", "preferences": {}})
    assert r.status_code == 200
    body = r.json()
    assert [x["region_key"] for x in body["regions"]] == ["greifenburg", "tolmin", "gemona"]
    assert all(x["total_score"] == 0.0 for x in body["regions"])

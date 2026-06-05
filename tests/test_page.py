"""Page render tests for GET / (aggregation patched — no DB)."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.routes as routes
from app.main import app

client = TestClient(app)

_FAKE = pd.DataFrame([
    {"region_key": "greifenburg", "flights_in_window": 1200, "flights_per_flyable_day": 23.0,
     "en_a_count": 100, "tandem_count": 18, "median_max_altitude": 2500.0, "p67_xc_points": 57.0,
     "expected_weekly_airtime_h": 12.0, "typical_weekly_airtime_h": 10.0, "p67_duration_sec": 7700.0,
     "years_covered": [2019, 2020]},
    {"region_key": "gemona", "flights_in_window": 40, "flights_per_flyable_day": 4.7,
     "en_a_count": 6, "tandem_count": 0, "median_max_altitude": 1700.0, "p67_xc_points": 58.0,
     "expected_weekly_airtime_h": 4.7, "typical_weekly_airtime_h": 4.0, "p67_duration_sec": 7900.0,
     "years_covered": [2019]},
])


@pytest.fixture(autouse=True)
def patch_aggregate(monkeypatch):
    monkeypatch.setattr(routes, "aggregate_for_date", lambda *a, **k: _FAKE.copy())


def test_page_renders_form_and_table():
    r = client.get("/", params={"date": "2026-06-15"})
    assert r.status_code == 200
    html = r.text
    assert 'name="expected_airtime"' in html  # weight slider per feature
    assert 'name="method"' in html            # method select
    assert "greifenburg" in html.lower() or "Greifenburg" in html


def test_page_accepts_weight_overrides():
    r = client.get("/", params={"date": "2026-06-15", "expected_airtime": "1", "flights_per_day": "-0.5"})
    assert r.status_code == 200

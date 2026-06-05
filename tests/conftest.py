"""Pytest configuration and fixtures for paraglider-vacations."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def region_meta():
    """Minimal region registry: name + car_hours (for the short_drive feature)."""
    return {
        "alpha": {"name": "Alpha", "travel_from_hannover": {"car_hours": 5.0}},
        "bravo": {"name": "Bravo", "travel_from_hannover": {"car_hours": 9.0}},
        "charlie": {"name": "Charlie", "travel_from_hannover": {"car_hours": 12.0}},
    }


def _row(region_key, **over):
    """A raw aggregation row with sane defaults, overridable per test."""
    base = {
        "region_key": region_key,
        "flights_in_window": 100,
        "flying_days": 50,
        "flyability": 0.8,
        "mean_duration_h": 1.5,
        "p67_duration_sec": 5400.0,
        "expected_weekly_airtime_h": 8.0,
        "flights_per_flyable_day": 5.0,
        "distinct_pilots": 40,
        "fai_triangle_count": 10,
        "en_a_count": 30,
        "en_b_count": 40,
        "tandem_count": 2,
        "p67_xc_points": 50.0,
        "median_max_altitude": 2000.0,
        "years_covered": [2019, 2020, 2021],
    }
    base.update(over)
    return base


@pytest.fixture
def make_row():
    return _row

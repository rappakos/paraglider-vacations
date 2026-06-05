"""
Seasonal aggregation middleware.

Maps every historical flight (2018–2025+) onto the **2026 calendar** and buckets
it into the ISO week whose midpoint (Thursday) lies within ``±window_days`` of the
flight's calendar position — see PLAN.md §7.

Cross-year matching is done on a *circular day-of-year* axis so that the same
slice of the season lines up regardless of the original year (leap years and the
Dec/Jan wrap are absorbed by the ±window tolerance).

For the moment this only produces the raw aggregation DataFrame — no feature
normalization / scoring (that lives in scoring.py later).
"""

import logging
from datetime import date, timedelta

import pandas as pd

from app.config import load_regions
from app.database import get_connection

logger = logging.getLogger(__name__)

REFERENCE_YEAR = 2026
DAYS_IN_YEAR = 366  # use the leap-year length as the circular modulus


# --------------------------------------------------------------------------- #
# Calendar helpers
# --------------------------------------------------------------------------- #

def build_week_calendar(year: int = REFERENCE_YEAR) -> pd.DataFrame:
    """
    One row per ISO week of ``year``: week number + Thursday midpoint.

    The Thursday is the ISO-canonical midpoint of a week, so a ±3 day window
    around it covers exactly Monday–Sunday.
    """
    weeks = []
    d = date(year, 1, 1)
    seen = set()
    # walk the whole year day by day, collect each (iso_year, iso_week) once
    while d.year <= year:
        iso = d.isocalendar()
        if iso.year == year and iso.week not in seen:
            seen.add(iso.week)
            # Thursday of this ISO week (isoweekday: Mon=1 .. Sun=7)
            thursday = d + timedelta(days=(4 - iso.weekday))
            weeks.append({"iso_week": iso.week, "week_midpoint": thursday})
        d += timedelta(days=1)
    cal = pd.DataFrame(weeks).sort_values("iso_week").reset_index(drop=True)
    cal["midpoint_doy"] = cal["week_midpoint"].map(lambda x: x.timetuple().tm_yday)
    return cal


def _circular_doy_distance(a: pd.Series, b: int) -> pd.Series:
    """Min forward/backward distance between day-of-year values on a circular axis."""
    raw = (a - b).abs()
    return raw.where(raw <= DAYS_IN_YEAR / 2, DAYS_IN_YEAR - raw)


def _candidate_years(midpoint: date, min_date: date, max_date: date) -> int:
    """
    How many historical years actually cover this week's window.

    A year counts only if its copy of the week midpoint falls inside the dataset's
    date coverage — so the partial current year is dropped for windows that lie
    beyond the last downloaded flight (avoids deflating flyability).
    """
    n = 0
    for y in range(min_date.year, max_date.year + 1):
        try:
            d = midpoint.replace(year=y)
        except ValueError:          # Feb 29 in a non-leap year
            d = midpoint.replace(year=y, day=28)
        if min_date <= d <= max_date:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #

def load_flights() -> pd.DataFrame:
    """Pull raw_flights into a DataFrame and attach derived calendar columns."""
    with get_connection() as conn:
        df = pd.read_sql_query("SELECT * FROM raw_flights", conn)

    df["flight_date"] = pd.to_datetime(df["flight_date"], format="%Y-%m-%d")
    df["src_year"] = df["flight_date"].dt.year
    # day-of-year on the original calendar; good enough for ±3d circular matching
    df["doy"] = df["flight_date"].dt.dayofyear
    return df


def site_to_region_map() -> dict[int, dict]:
    """site_id → {region_key, region_name} lookup built from regions.json."""
    regions = load_regions()
    mapping: dict[int, dict] = {}
    for key, meta in regions.items():
        for sid in meta["dhv_site_ids"]:
            mapping[sid] = {"region_key": key, "region_name": meta["name"]}
    return mapping


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def aggregate(
    window_days: int = 3,
    year: int = REFERENCE_YEAR,
    weeks: list[int] | None = None,
) -> pd.DataFrame:
    """
    Bucket all historical flights into ``year`` ISO weeks (±window_days) per region.

    Returns one row per (iso_week, region) with the raw ingredients for scoring.
    With ``window_days=3`` the windows tile the year exactly (each flight lands in
    one week); widening it makes neighbouring weeks overlap and a flight may count
    toward more than one week.

    ``weeks`` restricts the output to those ISO week numbers (cheap single-week
    path for the frontend); ``None`` computes every week.
    """
    flights = load_flights()
    cal = build_week_calendar(year)
    region_map = site_to_region_map()

    # keep only flights whose site belongs to a configured region
    flights = flights[flights["dhv_site_id"].isin(region_map)].copy()
    flights["region_key"] = flights["dhv_site_id"].map(lambda s: region_map[s]["region_key"])
    flights["region_name"] = flights["dhv_site_id"].map(lambda s: region_map[s]["region_name"])
    flights["flight_day"] = flights["flight_date"].dt.date  # actual calendar date, for distinct flyable days

    # dataset coverage — drives the flyability denominator
    min_date = flights["flight_date"].min().date()
    max_date = flights["flight_date"].max().date()
    window_span = 2 * window_days + 1  # calendar days the ±window covers in one year

    rows = []
    for _, wk in cal.iterrows():
        if weeks is not None and int(wk["iso_week"]) not in weeks:
            continue
        in_window = flights[
            _circular_doy_distance(flights["doy"], wk["midpoint_doy"]) <= window_days
        ]
        if in_window.empty:
            continue

        # day-slots actually observed across history for this week (region-independent)
        observed_day_slots = _candidate_years(wk["week_midpoint"], min_date, max_date) * window_span

        grouped = in_window.groupby(["region_key", "region_name"], observed=True)
        for (region_key, region_name), g in grouped:
            n = len(g)
            flying_days = g["flight_day"].nunique()
            flyability = min(1.0, flying_days / observed_day_slots) if observed_day_slots else 0.0
            mean_duration_sec = float(g["flight_duration_sec"].mean())
            # per-pilot median duration: robust to a few hyperactive uploaders
            typical_duration_sec = float(g.groupby("pilot_id")["flight_duration_sec"].mean().median())

            rows.append(
                {
                    "year": year,
                    "iso_week": int(wk["iso_week"]),
                    #"week_midpoint": wk["week_midpoint"],
                    "region_key": region_key,
                    #"region_name": region_name,
                    # --- coverage / flyability ---
                    "flights_in_window": n,
                    "flying_days": flying_days,
                    "observed_day_slots": observed_day_slots,
                    "flyability": round(flyability, 3),
                    # --- airtime ---
                    "mean_duration_h": round(mean_duration_sec / 3600, 2),
                    "p67_duration_sec": float(g["flight_duration_sec"].quantile(0.67)),
                    "expected_weekly_airtime_h": round(flyability * mean_duration_sec * 7 / 3600, 1),
                    "typical_weekly_airtime_h": round(flyability * typical_duration_sec * 7 / 3600, 1),
                    # --- crowd density (per flyable day, not raw cross-year counts) ---
                    "flights_per_flyable_day": round(n / flying_days, 1) if flying_days else 0.0,
                    "distinct_pilots": int(g["pilot_id"].nunique()),
                    # --- raw style / pilot-profile counts (the scoring layer derives ratios) ---
                    "fai_triangle_count": int((g["best_task_type_key"] == "FAI_TRIANGLE").sum()),
                    "en_a_count": int((g["glider_class"] == "EN A").sum()),
                    "en_b_count": int((g["glider_class"] == "EN B").sum()),
                    "tandem_count": int((g["competition_class"] == "Tandem").sum()),
                    "p67_xc_points": float(g["best_task_points"].quantile(0.67)),
                    "median_max_altitude": float(g["max_altitude"].median()),
                    "years_covered": sorted(int(y) for y in g["src_year"].unique()),
                }
            )

    return pd.DataFrame(rows)


def aggregate_for_date(
    target: date,
    window_days: int = 3,
    sort_by: str = "p67_duration_sec",
    year: int = REFERENCE_YEAR,
) -> pd.DataFrame:
    """
    Aggregation for the single ISO week containing ``target``, one row per region,
    sorted by ``sort_by`` descending. This is the frontend entry point.
    """
    iso_week = target.isocalendar().week
    df = aggregate(window_days=window_days, year=year, weeks=[iso_week])
    if not df.empty and sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=False).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(description="Aggregate historical flights onto 2026 ISO weeks.")
    parser.add_argument("--window", type=int, default=3, help="±days around each week midpoint (default 3).")
    parser.add_argument("--year", type=int, default=REFERENCE_YEAR, help="Reference calendar year (default 2026).")
    parser.add_argument("--week", type=int, default=None, help="Show only this ISO week.")
    parser.add_argument("--region", default=None, help="Show only this region_key.")
    args = parser.parse_args()

    df = aggregate(window_days=args.window, year=args.year)
    if args.week is not None:
        df = df[df["iso_week"] == args.week]
    if args.region:
        df = df[df["region_key"] == args.region]

    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 200)
    print(f"\n{len(df)} (week × region) buckets  |  window=±{args.window}d  year={args.year}\n")
    print(df.to_string(index=False))

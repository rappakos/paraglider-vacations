"""
Scoring / ranking layer.

Turns the raw per-(week, region) aggregation rows into a ranked region matrix,
driven by per-feature preference weights. Two interchangeable strategies:

  * "minmax" — Min-Max normalize each feature across the region set, weighted sum.
  * "rrf"    — Reciprocal Rank Fusion: rank per feature, weighted 1/(k+rank).

Feature *representation* (ratio vs level), *polarity* (higher/lower-is-better),
normalization and weighting all live HERE — the aggregation layer stays raw.
"""

import math
from dataclasses import dataclass
from typing import Callable

from app.models import DataCoverage, FeatureScore, RegionRecommendation

RRF_K = 10  # small region set (≤~20) → low fusion constant for meaningful rank separation

# --------------------------------------------------------------------------- #
# Feature registry
# --------------------------------------------------------------------------- #

Row = dict
RegionMeta = dict


@dataclass(frozen=True)
class Feature:
    key: str
    higher_is_better: bool
    source: str                              # "row" | "regions"
    derive: Callable[[Row, RegionMeta], float]   # (agg_row, region_meta) -> float | nan


def _safe(row: Row, col: str) -> float:
    v = row.get(col)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return math.nan
    return float(v)


def _ratio(num_col: str, den_col: str) -> Callable[[Row, RegionMeta], float]:
    def f(row: Row, meta: RegionMeta) -> float:
        den = row.get(den_col)
        num = row.get(num_col)
        if not den or num is None or (isinstance(num, float) and math.isnan(num)):
            return math.nan
        return num / den
    return f


def _car_hours(row: Row, meta: RegionMeta) -> float:
    try:
        return float(meta["travel_from_hannover"]["car_hours"])
    except (KeyError, TypeError, ValueError):
        return math.nan


FEATURE_REGISTRY: dict[str, Feature] = {
    "xc_style":          Feature("xc_style", True, "row", _ratio("fai_triangle_count", "flights_in_window")),
    "low_crowds":        Feature("low_crowds", False, "row", lambda r, m: _safe(r, "flights_per_flyable_day")),
    "beginner_friendly": Feature("beginner_friendly", True, "row", _ratio("en_a_count", "flights_in_window")),
    "alpine_ceiling":    Feature("alpine_ceiling", True, "row", lambda r, m: _safe(r, "median_max_altitude")),
    "short_drive":       Feature("short_drive", False, "regions", _car_hours),
    "airtime":           Feature("airtime", True, "row", lambda r, m: _safe(r, "expected_weekly_airtime_h")),
}

# Profile applied when the caller supplies no weights (the plain GET path).
DEFAULT_WEIGHTS: dict[str, float] = {"airtime": 1.0}


def feature_keys() -> list[str]:
    """All known feature keys, in registry order (for forms / CLI)."""
    return list(FEATURE_REGISTRY)


# --------------------------------------------------------------------------- #
# Ranking helpers
# --------------------------------------------------------------------------- #

def _average_ranks(values: dict[str, float], higher_is_better: bool) -> dict[str, float]:
    """
    1-based ranks (1 = best) with AVERAGE rank for ties; NaN values always rank
    worst (and share the averaged worst positions).
    """
    def sort_key(rk: str):
        v = values[rk]
        if math.isnan(v):
            return math.inf                       # worst, regardless of polarity
        return -v if higher_is_better else v      # smaller key = better position

    ordered = sorted(values, key=sort_key)
    ranks: dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and sort_key(ordered[j + 1]) == sort_key(ordered[i]):
            j += 1
        avg = (i + 1 + j + 1) / 2                  # mean of 1-based positions i..j
        for r in ordered[i:j + 1]:
            ranks[r] = avg
        i = j + 1
    return ranks


def _coverage(row: Row) -> DataCoverage:
    return DataCoverage(
        flights_in_window=int(row.get("flights_in_window", 0) or 0),
        years_covered=list(row.get("years_covered", []) or []),
    )


def _finalize(scored: list[dict], region_meta: RegionMeta) -> list[RegionRecommendation]:
    """Sort by total_score desc (tiebreak flights desc, key asc), assign ranks."""
    scored.sort(
        key=lambda s: (-s["total_score"], -s["flights_in_window"], s["region_key"])
    )
    out = []
    for i, s in enumerate(scored, start=1):
        meta = region_meta.get(s["region_key"], {})
        out.append(
            RegionRecommendation(
                region_key=s["region_key"],
                name=meta.get("name", s["region_key"]),
                rank=i,
                total_score=round(s["total_score"], 4),
                features=s["features"],
                data_coverage=_coverage(s["row"]),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #

def _raw_matrix(rows: list[Row], active: list[str], region_meta: RegionMeta) -> dict[str, dict[str, float]]:
    """raw[region_key][feature_key] = derived value (may be NaN)."""
    raw: dict[str, dict[str, float]] = {}
    for row in rows:
        rk = row["region_key"]
        meta = region_meta.get(rk, {})
        raw[rk] = {f: FEATURE_REGISTRY[f].derive(row, meta) for f in active}
    return raw


def _minmax(rows, weights, active, region_meta):
    raw = _raw_matrix(rows, active, region_meta)
    keys = [r["region_key"] for r in rows]
    w_sum = sum(weights[f] for f in active)

    # per-feature [0,1] normalized scores
    norm: dict[str, dict[str, float]] = {rk: {} for rk in keys}
    for f in active:
        feat = FEATURE_REGISTRY[f]
        vals = [raw[rk][f] for rk in keys if not math.isnan(raw[rk][f])]
        lo, hi = (min(vals), max(vals)) if vals else (math.nan, math.nan)
        for rk in keys:
            x = raw[rk][f]
            if math.isnan(x):
                norm[rk][f] = 0.0                 # underiveable -> worst
            elif hi == lo:
                norm[rk][f] = 0.5                 # degenerate -> neutral
            else:
                n = (x - lo) / (hi - lo)
                norm[rk][f] = n if feat.higher_is_better else 1.0 - n

    scored = []
    for row in rows:
        rk = row["region_key"]
        total = sum(weights[f] * norm[rk][f] for f in active) / w_sum if w_sum else 0.0
        features = {
            f: FeatureScore(
                raw_value=None if math.isnan(raw[rk][f]) else round(raw[rk][f], 4),
                normalized_score=round(norm[rk][f], 4),
            )
            for f in active
        }
        scored.append({"region_key": rk, "row": row, "total_score": total,
                       "flights_in_window": row.get("flights_in_window", 0), "features": features})
    return scored


def _rrf(rows, weights, active, region_meta, k=RRF_K):
    raw = _raw_matrix(rows, active, region_meta)
    keys = [r["region_key"] for r in rows]
    n = len(keys)

    ranks: dict[str, dict[str, float]] = {f: _average_ranks({rk: raw[rk][f] for rk in keys},
                                                            FEATURE_REGISTRY[f].higher_is_better)
                                          for f in active}
    # normalize total by the best achievable score (rank 1 in every active feature)
    max_score = sum(weights[f] * 1.0 / (k + 1) for f in active)

    scored = []
    for row in rows:
        rk = row["region_key"]
        rrf_score = sum(weights[f] * 1.0 / (k + ranks[f][rk]) for f in active)
        total = rrf_score / max_score if max_score else 0.0
        features = {
            f: FeatureScore(
                raw_value=None if math.isnan(raw[rk][f]) else round(raw[rk][f], 4),
                normalized_score=round((n - ranks[f][rk]) / (n - 1), 4) if n > 1 else 1.0,
            )
            for f in active
        }
        scored.append({"region_key": rk, "row": row, "total_score": total,
                       "flights_in_window": row.get("flights_in_window", 0), "features": features})
    return scored


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def rank_regions(
    rows: list[Row],
    weights: dict[str, float],
    region_meta: RegionMeta,
    method: str = "minmax",
    k: int = RRF_K,
) -> list[RegionRecommendation]:
    """
    Rank the regions for one week. ``rows`` are raw aggregation dicts (one per
    region); ``weights`` map feature key -> weight in [0,1].
    """
    if not rows:
        return []

    active = [f for f, w in weights.items() if w and w > 0 and f in FEATURE_REGISTRY]

    if not active:
        # nothing to score on — order by coverage, neutral scores
        scored = [{"region_key": r["region_key"], "row": r, "total_score": 0.0,
                   "flights_in_window": r.get("flights_in_window", 0), "features": {}}
                  for r in rows]
        return _finalize(scored, region_meta)

    if method == "rrf":
        scored = _rrf(rows, weights, active, region_meta, k=k)
    else:
        scored = _minmax(rows, weights, active, region_meta)

    return _finalize(scored, region_meta)


# --------------------------------------------------------------------------- #
# CLI — terminal review of ranked results
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse
    from datetime import date, datetime

    from app.config import REFERENCE_YEAR, load_regions
    from app.services.aggregation import aggregate_for_date

    parser = argparse.ArgumentParser(description="Rank regions for the ISO week of a date.")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD", help="Target date (default: today).")
    parser.add_argument("--method", choices=["minmax", "rrf"], default="minmax")
    parser.add_argument("--window", type=int, default=3, help="±days around the week midpoint (default 3).")
    parser.add_argument(
        "--weight", action="append", default=[], metavar="KEY=VALUE",
        help=f"Repeatable feature weight, e.g. --weight xc_style=0.8. "
             f"Keys: {', '.join(feature_keys())}. Default: {DEFAULT_WEIGHTS}.",
    )
    args = parser.parse_args()

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        today = date.today()
        target = today if today.year == REFERENCE_YEAR else date(REFERENCE_YEAR, today.month, today.day)

    if args.weight:
        weights = {}
        for item in args.weight:
            key, _, val = item.partition("=")
            weights[key.strip()] = float(val)
    else:
        weights = dict(DEFAULT_WEIGHTS)

    df = aggregate_for_date(target, window_days=args.window)
    ranked = rank_regions(df.to_dict("records"), weights, load_regions(), method=args.method)

    active = list(ranked[0].features.keys()) if ranked else []
    print(f"\nISO week {target.isocalendar().week} ({target})  |  method={args.method}  |  weights={weights}\n")
    header = f"{'#':>2}  {'region':<13}{'score':>7}  " + "  ".join(f"{k:>20}" for k in active)
    print(header)
    print("-" * len(header))
    for r in ranked:
        cells = []
        for k in active:
            fs = r.features[k]
            raw = "—" if fs.raw_value is None else f"{fs.raw_value:g}"
            cells.append(f"{fs.normalized_score:.2f} ({raw})".rjust(20))
        print(f"{r.rank:>2}  {r.region_key:<13}{r.total_score:>7.3f}  " + "  ".join(cells))
    print()

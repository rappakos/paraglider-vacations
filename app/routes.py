"""Web + API routes for the vacation recommender."""

import pathlib
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_PREFIX, REFERENCE_YEAR, load_regions
from app.models import RecommendRequest, RecommendResponse
from app.services.aggregation import aggregate_for_date
from app.services.scoring import DEFAULT_WEIGHTS, FEATURE_REGISTRY, rank_regions

PROJECT_ROOT = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

api_router = APIRouter(prefix=APP_PREFIX + "/api", tags=["vacations"])
page_router = APIRouter(prefix=APP_PREFIX, tags=["pages"])


def _parse_date(value: Optional[str]) -> date:
    """Parse YYYY-MM-DD, defaulting to today (clamped to the reference year)."""
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    today = date.today()
    return today if today.year == REFERENCE_YEAR else date(REFERENCE_YEAR, today.month, today.day)


def _weights_from_query(params) -> dict[str, float]:
    """
    Feature weights from query params; clamped to [0,1]. Any feature present in the
    query (even 0) is taken verbatim; if NO feature key is present at all, fall back
    to DEFAULT_WEIGHTS (the airtime-only profile).
    """
    present = {k: params[k] for k in FEATURE_REGISTRY if k in params}
    if not present:
        return dict(DEFAULT_WEIGHTS)
    weights: dict[str, float] = {}
    for key, raw in present.items():
        try:
            weights[key] = max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            continue
    return weights


def _method_from_query(params) -> str:
    return "rrf" if params.get("method") == "rrf" else "minmax"


@api_router.get("/recommend", response_model=RecommendResponse)
async def api_recommend(request: Request):
    """Ranked region matrix for the ISO week of `date`. Defaults to the airtime-only
    profile; override per-feature with `?<feature>=<0..1>` and `?method=minmax|rrf`."""
    target = _parse_date(request.query_params.get("date"))
    weights = _weights_from_query(request.query_params)
    method = _method_from_query(request.query_params)
    df = aggregate_for_date(target, window_days=3)
    ranked = rank_regions(df.to_dict("records"), weights, load_regions(), method=method)
    return RecommendResponse(
        calendar_week=target.isocalendar().week,
        year=REFERENCE_YEAR,
        method=method,
        weights=weights,
        regions=ranked,
    )


@api_router.post("/recommend", response_model=RecommendResponse)
async def api_recommend_post(body: RecommendRequest):
    """Ranked region matrix for the ISO week of `date`, weighted by preferences."""
    target = _parse_date(body.date)
    df = aggregate_for_date(target, window_days=body.window_days or 3)
    weights = {key: pref.weight for key, pref in body.preferences.items()}
    ranked = rank_regions(df.to_dict("records"), weights, load_regions(), method=body.method)
    return RecommendResponse(
        calendar_week=target.isocalendar().week,
        year=REFERENCE_YEAR,
        method=body.method,
        weights=weights,
        regions=ranked,
    )


@page_router.get("/")
async def index(request: Request):
    """Weights/method form + a ranked grid showing per-feature raw & normalized scores."""
    target = _parse_date(request.query_params.get("date"))
    weights = _weights_from_query(request.query_params)
    method = _method_from_query(request.query_params)
    df = aggregate_for_date(target, window_days=3)
    regions = rank_regions(df.to_dict("records"), weights, load_regions(), method=method)
    active_features = list(regions[0].features.keys()) if regions else []
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": "Paraglider Vacations",
            "date": target.isoformat(),
            "iso_week": target.isocalendar().week,
            "method": method,
            "feature_keys": list(FEATURE_REGISTRY),
            "weights": {k: weights.get(k, 0.0) for k in FEATURE_REGISTRY},
            "active_features": active_features,
            "regions": regions,
        },
    )


def setup_routes(app):
    app.mount(APP_PREFIX + "/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
    app.include_router(api_router)
    app.include_router(page_router)

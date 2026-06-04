"""Web + API routes for the vacation recommender."""

import math
import pathlib
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_PREFIX, REFERENCE_YEAR
from app.services.aggregation import aggregate_for_date

PROJECT_ROOT = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

SORT_BY = "p67_duration_sec"

api_router = APIRouter(prefix=APP_PREFIX + "/api", tags=["vacations"])
page_router = APIRouter(prefix=APP_PREFIX, tags=["pages"])


def _parse_date(value: Optional[str]) -> date:
    """Parse YYYY-MM-DD, defaulting to today (clamped to the reference year)."""
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    today = date.today()
    return today if today.year == REFERENCE_YEAR else date(REFERENCE_YEAR, today.month, today.day)


def _clean(records: list[dict]) -> list[dict]:
    """Round floats and replace NaN with None so the table/JSON stay tidy."""
    out = []
    for row in records:
        clean = {}
        for k, v in row.items():
            if isinstance(v, float):
                clean[k] = None if math.isnan(v) else round(v, 1)
            else:
                clean[k] = v
        out.append(clean)
    return out


@api_router.get("/recommend")
async def api_recommend(date: Optional[str] = Query(None, description="YYYY-MM-DD")):
    """Aggregated region columns for the ISO week of `date`, sorted by airtime p67."""
    target = _parse_date(date)
    df = aggregate_for_date(target, sort_by=SORT_BY)
    return {
        "date": target.isoformat(),
        "iso_week": target.isocalendar().week,
        "sort_by": SORT_BY,
        "columns": list(df.columns),
        "regions": _clean(df.to_dict("records")),
    }


@page_router.get("/")
async def index(request: Request, date: Optional[str] = Query(None, description="YYYY-MM-DD")):
    """Date picker + a simple grid of the aggregated columns for that week."""
    target = _parse_date(date)
    df = aggregate_for_date(target, sort_by=SORT_BY)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": "Paraglider Vacations",
            "date": target.isoformat(),
            "iso_week": target.isocalendar().week,
            "sort_by": SORT_BY,
            "columns": list(df.columns),
            "rows": _clean(df.to_dict("records")),
        },
    )


def setup_routes(app):
    app.mount(APP_PREFIX + "/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")
    app.include_router(api_router)
    app.include_router(page_router)

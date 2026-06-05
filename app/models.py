"""Pydantic request/response models for the /recommend endpoint (PLAN.md §4)."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class PreferenceWeight(BaseModel):
    weight: float = Field(0.0, ge=-1.0, le=1.0)   # signed: + want more, − want less


class RecommendRequest(BaseModel):
    date: str                                                  # "YYYY-MM-DD"
    preferences: dict[str, PreferenceWeight] = Field(default_factory=dict)
    method: Literal["minmax", "rrf"] = "minmax"
    window_days: Optional[int] = Field(3, ge=0, le=14)


class FeatureScore(BaseModel):
    raw_value: Optional[float] = None                          # null when underiveable (NaN)
    normalized_score: float                                    # [0,1]; rank-derived for rrf


class DataCoverage(BaseModel):
    flights_in_window: int
    years_covered: list[int]


class RegionRecommendation(BaseModel):
    region_key: str
    name: str
    rank: int
    total_score: float
    features: dict[str, FeatureScore]
    data_coverage: DataCoverage


class RecommendResponse(BaseModel):
    calendar_week: int
    year: int
    method: str
    weights: dict[str, float] = Field(default_factory=dict)
    regions: list[RegionRecommendation]

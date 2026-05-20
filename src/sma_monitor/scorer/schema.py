"""Scorer I/O types.

AxisScores       — what the LLM (or heuristic) returns: three axes + rationale + confidence
CompositeScore   — fully-resolved row: axes + multipliers + composite + band
ScoreCandidate   — input bundle assembled by the pipeline before scoring
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ThresholdBand = Literal["above_t", "t2_to_t", "below_t2"]


class AxisScores(BaseModel):
    """Direct output of the LLM / heuristic scorer."""

    financial_impact: float = Field(ge=0, le=10)
    narrative_shift: float = Field(ge=0, le=10)
    time_criticality: float = Field(ge=0, le=10)
    rationale: str
    confidence: float = Field(ge=0, le=1)


class ScoreCandidate(BaseModel):
    """Everything needed to score one (article, ticker) pair."""

    # Article
    article_event_id: str
    title: str
    excerpt: str
    source: str | None
    source_tier: int | None
    published_at: datetime | None
    # Holding context (from Phase 1 joined view)
    ticker: str
    pct_nav: float
    conviction_tier: int
    stage: str
    thesis: str
    nearest_catalyst_days: int | None
    # Bucket tags (from Phase 2)
    primary_bucket_id: int
    primary_bucket_name: str
    primary_bucket_confidence: float
    secondary_buckets: list[tuple[int, float]] = Field(default_factory=list)


class CompositeScore(BaseModel):
    """Fully-resolved score row. Persisted into scores table."""

    article_event_id: str
    ticker: str
    primary_bucket_id: int
    secondary_buckets: list[tuple[int, float]]

    financial_impact: float
    narrative_shift: float
    time_criticality: float
    raw_avg: float

    bucket_weight: float
    position_weight: float
    conviction_mult: float
    catalyst_boost: float
    stage_interaction: float
    composite: float
    threshold_band: ThresholdBand

    rationale: str
    confidence: float

    model_used: str
    multipliers_version: str
    inputs_hash: str
    scored_at: datetime

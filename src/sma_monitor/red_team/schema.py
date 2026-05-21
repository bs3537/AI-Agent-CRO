"""Red team I/O types.

RedTeamCandidate — pipeline-assembled input bundle (one per score >= T₂)
RedTeamResult    — LLM/heuristic output: bearish thesis, cited patterns
MatchedWarningSign — one cited pattern with fit_strength
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# One warning-sign citation. The red team must cite a real catalog id —
# fabricated ids are silently dropped by claude_client._parse_result.
class MatchedWarningSign(BaseModel):
    id: str
    name: str = ""
    bucket_id: int
    fit_strength: float = Field(ge=0, le=1)


# Direct output of the red team — bearish thesis plus zero-or-more cited
# patterns, the buckets they evidence, severity 1–5, and an invalidator.
class RedTeamResult(BaseModel):
    """Direct output of the red team — what the scorer / heuristic produces."""

    bearish_thesis: str
    matched_warning_signs: list[MatchedWarningSign] = Field(default_factory=list)
    matched_buckets: list[int] = Field(default_factory=list)
    severity_of_concern: int = Field(ge=1, le=5)
    invalidator: str
    confidence: float = Field(ge=0, le=1)


# Everything the red team needs to argue the short case for one score.
# Includes the scorer's view so the red team can challenge or amplify.
class RedTeamCandidate(BaseModel):
    """Everything the red team needs to argue the short case for one score."""

    # Score / article
    score_event_id: str
    article_event_id: str
    title: str
    excerpt: str
    source: str | None
    source_tier: int | None
    published_at: datetime | None
    # Holding context (from Phase 1 joined view)
    ticker: str
    company_name: str | None
    pct_nav: float
    conviction_tier: int
    stage: str
    thesis: str
    nearest_catalyst_days: int | None
    # Bucket
    primary_bucket_id: int
    primary_bucket_name: str
    # Scorer's view (so red team can challenge / amplify)
    composite: float
    threshold_band: str
    scorer_axes: tuple[float, float, float]  # (financial_impact, narrative_shift, time_criticality)
    scorer_rationale: str
    scorer_confidence: float

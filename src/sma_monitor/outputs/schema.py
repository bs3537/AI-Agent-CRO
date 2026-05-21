"""Output records.

AlertRecord     — fully resolved alert (score + red team + holding context)
DigestEvent     — one row in the digest "today's events" table
DigestSummary   — full digest payload (sections + optional narrative)
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# Fully-resolved alert payload. Everything format.render_alert() needs to
# emit mobile-glanceable text in one pass. red_team fields are optional
# because a score above T may not have a pass yet at the current catalog.
class AlertRecord(BaseModel):
    """Everything format.render_alert() needs to produce alert text."""

    # Identity
    score_event_id: str
    article_event_id: str
    red_team_event_id: str | None = None
    # Article
    article_title: str
    article_url: str
    source: str | None
    source_tier: int | None
    # Holding
    ticker: str
    company_name: str | None = None
    pct_nav: float
    conviction_tier: int
    stage: str
    nearest_catalyst_days: int | None
    # Bucket
    bucket_id: int
    bucket_short_name: str
    # Scores
    composite: float
    threshold_band: str
    axes: tuple[float, float, float]  # (financial_impact, narrative_shift, time_criticality)
    scorer_rationale: str
    # Red team (optional — None if no red-team pass at this catalog_version)
    bearish_thesis: str | None = None
    severity_of_concern: int | None = None
    matched_warning_signs: list[dict] = Field(default_factory=list)
    invalidator: str | None = None


# One row in the digest's "today's events" table. Same shape as AlertRecord
# but without the identity/URL fields needed for alert routing.
class DigestEvent(BaseModel):
    """One row in the digest's events table."""

    ticker: str
    composite: float
    threshold_band: str
    bucket_id: int
    bucket_short_name: str
    title: str
    url: str
    source: str | None
    source_tier: int | None
    axes: tuple[float, float, float]
    scorer_rationale: str
    bearish_thesis: str | None
    severity_of_concern: int | None
    matched_warning_signs: list[dict]


# Compact holding snapshot used in the "quiet holdings" section of the
# digest. Just enough fields to flag near catalysts and overdue ones.
class HoldingSnapshot(BaseModel):
    ticker: str
    pct_nav: float
    conviction_tier: int
    stage: str
    nearest_catalyst_days: int | None
    has_overdue_catalyst: bool


# Full digest payload assembled by digest.py before rendering. Sections
# map to the markdown headers in format.render_digest_markdown().
class DigestSummary(BaseModel):
    """Full assembled digest before rendering."""

    date: str
    events_by_ticker: dict[str, list[DigestEvent]]
    quiet_holdings: list[HoldingSnapshot]
    bucket_concentrations: list[tuple[int, int]]  # (bucket_id, n_tickers_hit_today)
    coverage_audit: dict[int, int]                # bucket_id → article count over 14d
    silent_buckets: list[int]
    narrative: str | None = None
    assembled_at: datetime

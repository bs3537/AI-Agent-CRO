"""API response/request models (Workstream 5).

These are the JSON shapes the React dashboard consumes. They are thin
projections of the internal pydantic models + SQLite rows — kept separate so
the wire contract can evolve independently of storage. open_pnl / pnl_pct are
computed here (market_value − cost_basis) per the W5 spec.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..decision.schema import Color, Verdict


# Latest thesis-drift decision for a position, as served to the dashboard.
class DecisionOut(BaseModel):
    verdict: Verdict
    color: Color
    note: str
    drivers: list[str] = Field(default_factory=list)
    confidence: float
    model_used: str
    decided_at: str


# One upcoming catalyst on a position.
class CatalystOut(BaseModel):
    date: str
    type: str
    description: str
    confidence: str
    resolved: bool = False


# One uploaded thesis document (metadata only; text is server-side).
class FileOut(BaseModel):
    event_id: str
    filename: str
    content_type: str
    n_chars: int
    byte_size: int
    uploaded_at: str


# One scored article in a position's detail view.
class ScoreOut(BaseModel):
    score_event_id: str
    title: str
    url: str | None
    primary_bucket_id: int
    composite: float
    threshold_band: str
    axes: tuple[float, float, float]   # (financial, narrative, time)
    rationale: str
    confidence: float
    scored_at: str


# One red-team bear case in a position's detail view.
class RedTeamOut(BaseModel):
    pass_event_id: str
    title: str
    url: str | None
    bearish_thesis: str
    severity_of_concern: int
    matched_patterns: list[str] = Field(default_factory=list)
    invalidator: str
    ran_at: str


# Row in the positions grid: economics + nearest catalyst + latest decision.
class PositionSummary(BaseModel):
    ticker: str
    company_name: str | None
    stage: str
    conviction_tier: int
    qty: float
    market_value: float
    cost_basis: float | None
    pct_nav: float
    open_pnl: float | None             # market_value − cost_basis
    pnl_pct: float | None
    nearest_catalyst_days: int | None
    has_overdue_catalyst: bool
    thesis: str
    n_files: int = 0
    decision: DecisionOut | None = None


# GET /api/positions envelope: the grid plus provenance + sidecar gaps.
class PositionsResponse(BaseModel):
    pulled_at: str | None
    positions: list[PositionSummary]
    missing_sidecars: list[str] = Field(default_factory=list)


# GET /api/positions/{ticker}: the summary plus the full evidence trail.
class PositionDetail(PositionSummary):
    catalysts: list[CatalystOut] = Field(default_factory=list)
    scores: list[ScoreOut] = Field(default_factory=list)
    red_team: list[RedTeamOut] = Field(default_factory=list)
    files: list[FileOut] = Field(default_factory=list)
    financials: dict[str, Any] | None = None   # W2 (FMP); null until wired


# PUT /api/positions/{ticker}/thesis request body.
class ThesisUpdate(BaseModel):
    thesis: str


# POST /api/positions/{ticker}/recompute response.
class RecomputeResponse(BaseModel):
    ticker: str
    scheduled: bool                    # True when run in the background
    decision: DecisionOut | None = None  # populated only when ?wait=true


# GET /api/status: operational snapshot wrapped from the orchestrator helpers.
class StatusOut(BaseModel):
    spend: dict[str, Any]
    degrade: dict[str, Any]
    spend_by_kind: list[dict[str, Any]] = Field(default_factory=list)
    flags: list[dict[str, Any]] = Field(default_factory=list)
    dead_letters: dict[str, Any] = Field(default_factory=dict)
    positions: dict[str, Any] = Field(default_factory=dict)

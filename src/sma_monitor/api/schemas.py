"""API response/request models (Workstream 5).

These are the JSON shapes the React dashboard consumes. They are thin
projections of the internal pydantic models + SQLite rows — kept separate so
the wire contract can evolve independently of storage. open_pnl / pnl_pct are
computed here (market_value − cost_basis) per the W5 spec.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..decision.schema import Action, AttentionState, Color, Grade, TechnicalState, Verdict

MAX_THESIS_WORDS = 2000


# Latest thesis-drift decision for a position, as served to the dashboard.
class DecisionOut(BaseModel):
    verdict: Verdict
    color: Color
    grade: str                         # A/B/C/D letter grade (see routes.positions._grade)
    note: str
    drivers: list[str] = Field(default_factory=list)
    confidence: float
    model_used: str
    compute_source: str = "unknown"
    decided_at: str


# Canonical V2 position rating. The frontend should prefer this over the legacy
# decision field; decision remains during migration for old callers.
class RatingOut(BaseModel):
    action: Action
    grade: Grade
    attention_state: AttentionState
    risk_score: float
    risk_components: dict[str, Any] = Field(default_factory=dict)
    latest_close: float | None = None
    ema20: float | None = None
    price_vs_ema20_pct: float | None = None
    technical_state: TechnicalState
    deterministic_grade: Grade
    llm_grade: Grade | None = None
    final_grade: Grade
    note: str
    drivers: list[str] = Field(default_factory=list)
    confidence: float
    model_used: str
    compute_source: str = "unknown"
    rating_version: str
    decided_at: str


# Sparkline payload: close line plus the 20-day EMA overlay, both oldest→newest.
class SparklineOut(BaseModel):
    closes: list[float]
    ema20: list[float | None] = Field(default_factory=list)
    latest_close: float | None = None
    latest_ema20: float | None = None
    price_vs_ema20_pct: float | None = None
    technical_state: TechnicalState = "no_price_data"


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
    spark: SparklineOut | None = None  # ~1yr daily EOD closes + EMA20 overlay
    rating: RatingOut | None = None
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

    @field_validator("thesis")
    @classmethod
    def thesis_word_limit(cls, value: str) -> str:
        if len(value.split()) > MAX_THESIS_WORDS:
            raise ValueError(f"thesis must be {MAX_THESIS_WORDS} words or fewer")
        return value


# POST /api/positions request body for manually adding a dashboard position.
class ManualPositionCreate(BaseModel):
    ticker: str
    portfolio_weight_pct: float = Field(ge=0, le=100, default=0.0)
    thesis: str
    cost_basis_per_share: float | None = Field(default=None, gt=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        ticker = value.strip().upper()
        if not ticker:
            raise ValueError("ticker is required")
        return ticker

    @field_validator("thesis")
    @classmethod
    def manual_thesis_word_limit(cls, value: str) -> str:
        if len(value.split()) > MAX_THESIS_WORDS:
            raise ValueError(f"thesis must be {MAX_THESIS_WORDS} words or fewer")
        return value


class ManualPositionResponse(BaseModel):
    position: PositionSummary
    refresh: dict[str, Any] | None = None


# POST /api/positions/{ticker}/recompute response.
class RecomputeResponse(BaseModel):
    ticker: str
    scheduled: bool                    # True when run in the background
    request_id: str | None = None       # populated when queued for Hermes/runner
    queue_status: str | None = None
    rating: RatingOut | None = None
    decision: DecisionOut | None = None  # populated only when ?wait=true
    refresh: dict[str, Any] | None = None


# POST /api/positions/recompute response — summary of a whole-portfolio run.
class RecomputeAllResponse(BaseModel):
    scheduled: bool                    # True when run in the background
    request_id: str | None = None       # populated when queued for Hermes/runner
    queue_status: str | None = None
    decided: int = 0                   # counts populated only when ?wait=true
    skipped: int = 0
    errors: int = 0
    holdings: int = 0
    refresh: dict[str, Any] | None = None


# DELETE /api/positions/{ticker} response.
class DeleteHoldingResponse(BaseModel):
    ticker: str
    deleted: dict[str, int] = Field(default_factory=dict)


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatAttachmentOut(BaseModel):
    filename: str
    content_type: str
    byte_size: int
    n_chars: int
    parser: str


class ChatResponse(BaseModel):
    answer: str
    model_used: str
    used_tickers: list[str] = Field(default_factory=list)
    cited_context: list[dict[str, Any]] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    attachments: list[ChatAttachmentOut] = Field(default_factory=list)


# GET /api/status: operational snapshot wrapped from the orchestrator helpers.
class StatusOut(BaseModel):
    spend: dict[str, Any]
    degrade: dict[str, Any]
    spend_by_kind: list[dict[str, Any]] = Field(default_factory=list)
    flags: list[dict[str, Any]] = Field(default_factory=list)
    dead_letters: dict[str, Any] = Field(default_factory=dict)
    positions: dict[str, Any] = Field(default_factory=dict)
    deployment: dict[str, Any] = Field(default_factory=dict)
    runner_requests: list[dict[str, Any]] = Field(default_factory=list)

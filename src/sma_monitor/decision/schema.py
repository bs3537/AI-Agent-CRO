"""Decision engine I/O types (Workstream 3 — thesis-drift).

DecisionCandidate — per-holding input bundle the engine assembles from the
                    joined view + recent scores + recent red-team passes.
PositionDecision  — engine output: HOLD/WATCH/SELL verdict, color, short note,
                    drivers, confidence, and the hashes that gate re-compute.
ScoreEvidence / BearEvidence — compact summaries of the evidence fed in.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# Thesis-drift verdict and its dashboard color. hold=thesis intact,
# watch=thesis under pressure, sell=thesis materially broken.
Verdict = Literal["hold", "watch", "sell"]
Color = Literal["green", "yellow", "red"]
Grade = Literal["A", "B", "C", "D"]
Action = Literal["hold", "sell"]
AttentionState = Literal["clean", "monitor", "watch", "broken"]
TechnicalState = Literal["above_ema20", "below_ema20", "extended_below_ema20", "no_price_data"]

# Canonical verdict→color map. The single source of truth for the chip color
# so the engine, API, and email never disagree.
VERDICT_COLOR: dict[str, Color] = {"hold": "green", "watch": "yellow", "sell": "red"}


# Canonical V2 rating maps. Grade is the source of truth; action/verdict/color
# derive from it so the dashboard cannot show contradictory signals.
GRADE_ACTION: dict[str, Action] = {"A": "hold", "B": "hold", "C": "hold", "D": "sell"}
GRADE_ATTENTION: dict[str, AttentionState] = {
    "A": "clean",
    "B": "monitor",
    "C": "watch",
    "D": "broken",
}
GRADE_VERDICT: dict[str, Verdict] = {"A": "hold", "B": "hold", "C": "watch", "D": "sell"}


# Daily technical snapshot used by both the rating engine and dashboard. The EMA
# series aligns 1:1 with closes; early values are None until the 20-day seed SMA.
class TechnicalSnapshot(BaseModel):
    closes: list[float] = Field(default_factory=list)
    ema20: list[float | None] = Field(default_factory=list)
    latest_close: float | None = None
    latest_ema20: float | None = None
    price_vs_ema20_pct: float | None = None
    consecutive_below_ema20: int = 0
    ema20_slope_5d: float | None = None
    technical_state: TechnicalState = "no_price_data"
    risk_points: int = 0


# One scored-article summary fed into the decision. A compact projection of a
# scores row so the prompt (and heuristic) see severity without the full text.
class ScoreEvidence(BaseModel):
    score_event_id: str
    title: str
    primary_bucket_id: int
    composite: float
    threshold_band: str
    rationale: str


# One red-team bear case fed into the decision — the short case plus which
# warning-sign patterns matched and how severe the concern is.
class BearEvidence(BaseModel):
    pass_event_id: str
    title: str
    bearish_thesis: str
    severity_of_concern: int
    matched_patterns: list[str] = Field(default_factory=list)
    invalidator: str


# Everything the engine needs to assess thesis drift for ONE holding. Built
# from latest_joined() + recent_scores() + recent_passes(). thesis_doc_text
# (W4 uploads) and fmp_metrics (W2) are forward-compat slots — empty/None
# until those workstreams land, so the bundle shape never has to change.
class DecisionCandidate(BaseModel):
    """Per-holding evidence bundle for one thesis-drift assessment."""

    # Holding identity + thesis
    ticker: str
    company_name: str | None
    stage: str
    conviction_tier: int
    thesis: str
    thesis_doc_text: str = ""          # W4: extracted text of uploaded thesis docs
    # Position economics
    pct_nav: float
    market_value: float
    cost_basis: float | None
    open_pnl: float | None
    pnl_pct: float | None
    # Catalysts (Phase 1 derived)
    nearest_catalyst_days: int | None
    has_overdue_catalyst: bool
    catalysts: list[str] = Field(default_factory=list)
    # Ingested evidence (Phases 3 + 4)
    scores: list[ScoreEvidence] = Field(default_factory=list)
    bears: list[BearEvidence] = Field(default_factory=list)
    fmp_metrics: dict | None = None    # W2: FMP fundamentals/ratios (#4/#7/#12)
    fmp_corroboration: dict | None = None  # native web-search corroboration handoff for FMP data (display-only)
    technical: TechnicalSnapshot | None = None  # Daily close vs 20-day EMA
    # Pre-reduced extremes fed to the heuristic fallback and LLM prompt.
    max_severity: int = 1
    max_composite: float = 0.0


# The engine's output for one holding, persisted to position_decisions. The
# note is a 4–5 line plain-English synthesis; drivers name the concrete
# evidence behind the verdict. thesis_hash + inputs_hash gate re-compute:
# inputs_hash changes whenever the thesis or the evidence set changes.
class PositionDecision(BaseModel):
    """Thesis-drift verdict for one holding. One row per (ticker, inputs_hash)."""

    ticker: str
    verdict: Verdict
    color: Color
    note: str
    drivers: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    thesis_hash: str
    inputs_hash: str
    model_used: str
    compute_source: str = "unknown"
    decided_at: datetime


# V2 canonical portfolio-facing rating. This is the dashboard/email source of
# truth; legacy hold/watch/sell is derived from grade during migration.
class PositionRating(BaseModel):
    ticker: str
    action: Action
    grade: Grade
    attention_state: AttentionState
    risk_score: float = Field(ge=0, le=100)
    risk_components: dict[str, Any] = Field(default_factory=dict)
    latest_close: float | None = None
    ema20: float | None = None
    price_vs_ema20_pct: float | None = None
    technical_state: TechnicalState = "no_price_data"
    deterministic_grade: Grade
    llm_grade: Grade | None = None
    final_grade: Grade
    note: str
    drivers: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    thesis_hash: str
    inputs_hash: str
    model_used: str
    compute_source: str = "unknown"
    rating_version: str
    decided_at: datetime

"""Workstream 3 — thesis-drift decision engine.

A per-position synthesis layered on top of the phase 1–7 pipeline. For each
holding it folds the long thesis together with the scored articles, red-team
bear cases, catalysts, open P&L, and EMA20 trend into one LLM-final A/B/C/D
rating plus a short note. Offline-first deterministic fallback remains
available when Codex is unavailable. Output persists to the legacy
position_decisions table and the V2 position_ratings table that powers the
synchronized grade/action dashboard.
"""
from .engine import DECISION_VERSION, build_candidate, decide, decide_with_grade, run_decisions
from .rating import RATING_VERSION, rate_candidate
from .schema import (
    VERDICT_COLOR,
    Action,
    BearEvidence,
    Color,
    DecisionCandidate,
    Grade,
    PositionDecision,
    PositionRating,
    ScoreEvidence,
    TechnicalSnapshot,
    Verdict,
)
from .store import (
    has_rating_for,
    init_decision_schema,
    latest_decision,
    latest_decisions,
    latest_rating,
    latest_ratings,
    save_decision,
    save_rating,
)

__all__ = [
    "DECISION_VERSION",
    "RATING_VERSION",
    "build_candidate",
    "decide",
    "decide_with_grade",
    "rate_candidate",
    "run_decisions",
    "Action",
    "BearEvidence",
    "Color",
    "DecisionCandidate",
    "Grade",
    "PositionDecision",
    "PositionRating",
    "ScoreEvidence",
    "TechnicalSnapshot",
    "Verdict",
    "VERDICT_COLOR",
    "has_rating_for",
    "init_decision_schema",
    "latest_decision",
    "latest_decisions",
    "latest_rating",
    "latest_ratings",
    "save_decision",
    "save_rating",
]

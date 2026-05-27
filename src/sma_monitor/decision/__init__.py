"""Workstream 3 — thesis-drift decision engine.

A per-position synthesis layered on top of the phase 1–7 pipeline. For each
holding it folds the long thesis together with the scored articles, red-team
bear cases, catalysts, and open P&L into a single HOLD / WATCH / SELL verdict
plus a short note. Offline-first (deterministic heuristic from red-team
severity + composite band) and wires the Codex LLM provider (W1) when present.
Output persists to the position_decisions table and powers both the web
dashboard and the 9 AM thesis-drift email.
"""
from .engine import DECISION_VERSION, build_candidate, decide, run_decisions
from .schema import (
    BearEvidence,
    Color,
    DecisionCandidate,
    PositionDecision,
    ScoreEvidence,
    Verdict,
    VERDICT_COLOR,
)
from .store import (
    init_decision_schema,
    latest_decision,
    latest_decisions,
    save_decision,
)

__all__ = [
    "DECISION_VERSION",
    "build_candidate",
    "decide",
    "run_decisions",
    "BearEvidence",
    "Color",
    "DecisionCandidate",
    "PositionDecision",
    "ScoreEvidence",
    "Verdict",
    "VERDICT_COLOR",
    "init_decision_schema",
    "latest_decision",
    "latest_decisions",
    "save_decision",
]

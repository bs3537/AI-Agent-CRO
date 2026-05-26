"""LLM-backed severity scoring.

Routes scoring through the pluggable LLM provider (Codex by default; see
`sma_monitor.llm`). The provider returns schema-conforming JSON which we
validate into AxisScores. When no provider is available the pipeline uses
the deterministic heuristic scorer instead — this module is never reached
in offline mode.
"""
from __future__ import annotations

from ..llm import LLMError, LLMProvider
from .prompt import SYSTEM_PROMPT, build_user_message
from .schema import AxisScores, ScoreCandidate

# Back-compat label persisted to scores.model_used. With Codex the concrete
# model (gpt-5.x) is whatever the logged-in account selects; we don't pin it.
DEFAULT_MODEL = "codex-cli"
MAX_TOKENS = 512

# Back-compat alias: existing `except ClaudeError` clauses keep working now
# that failures surface as the provider-neutral LLMError.
ClaudeError = LLMError

# JSON Schema the provider constrains scoring output to. Mirrors AxisScores.
AXIS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "financial_impact": {"type": "number", "minimum": 0, "maximum": 10},
        "narrative_shift": {"type": "number", "minimum": 0, "maximum": 10},
        "time_criticality": {"type": "number", "minimum": 0, "maximum": 10},
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "financial_impact",
        "narrative_shift",
        "time_criticality",
        "rationale",
        "confidence",
    ],
}


# Score one candidate via the LLM provider. Returns (axes, model_used) so the
# caller can persist which model produced the row, and records the call to the
# Phase 6 cost ledger as a side effect.
def score_with_llm(
    candidate: ScoreCandidate,
    *,
    provider: LLMProvider,
    model_label: str | None = None,
) -> tuple[AxisScores, str]:
    """Returns (axis_scores, model_used_str)."""
    data = provider.complete_json(
        system=SYSTEM_PROMPT,
        user=build_user_message(candidate),
        schema=AXIS_SCHEMA,
        max_tokens=MAX_TOKENS,
    )
    axes = AxisScores.model_validate(data)
    # Phase 6: record the call for the cost ledger / status view.
    try:
        from ..orchestrator.cost import record_llm_call
        record_llm_call(kind="score", model=provider.model_label,
                        related_event_id=candidate.article_event_id)
    except Exception:
        pass  # cost recording must never break the scoring path
    return axes, model_label or provider.model_label

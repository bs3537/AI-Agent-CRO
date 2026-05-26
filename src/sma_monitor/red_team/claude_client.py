"""LLM-backed red team.

Routes the red-team pass through the pluggable LLM provider (Codex by
default; see `sma_monitor.llm`). The system prompt carries the full warning-
signs catalog. Output is schema-conforming JSON; we then drop any cited
warning-sign id that isn't in the catalog (voice constraint: cite real ids
only) and enrich the rest with their canonical name + bucket.
"""
from __future__ import annotations

from ..llm import LLMError, LLMProvider
from .catalog import Catalog, by_id
from .prompt import build_system_prompt, build_user_message
from .schema import MatchedWarningSign, RedTeamCandidate, RedTeamResult

# Back-compat label persisted to red_team_passes.model_used.
DEFAULT_MODEL = "codex-cli"
MAX_TOKENS = 800

# Back-compat alias so existing `except RedTeamClaudeError` clauses keep working.
RedTeamClaudeError = LLMError

# JSON Schema the provider constrains red-team output to. Matched warning
# signs carry only id + fit_strength here; name/bucket are enriched from the
# catalog afterward so the model can't fabricate them.
RED_TEAM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "bearish_thesis": {"type": "string"},
        "matched_warning_signs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "fit_strength": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["id", "fit_strength"],
            },
        },
        "matched_buckets": {"type": "array", "items": {"type": "integer"}},
        "severity_of_concern": {"type": "integer", "minimum": 1, "maximum": 5},
        "invalidator": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "bearish_thesis",
        "matched_warning_signs",
        "matched_buckets",
        "severity_of_concern",
        "invalidator",
        "confidence",
    ],
}


# Run one red-team pass via the LLM provider. Returns (result, model_used) and
# records the call to the Phase 6 cost ledger as a side effect.
def red_team_with_llm(
    candidate: RedTeamCandidate,
    catalog: Catalog,
    *,
    provider: LLMProvider,
    model_label: str | None = None,
) -> tuple[RedTeamResult, str]:
    data = provider.complete_json(
        system=build_system_prompt(catalog),
        user=build_user_message(candidate),
        schema=RED_TEAM_SCHEMA,
        max_tokens=MAX_TOKENS,
    )
    try:
        from ..orchestrator.cost import record_llm_call
        record_llm_call(kind="red_team", model=provider.model_label,
                        related_event_id=candidate.article_event_id)
    except Exception:
        pass
    return _build_result(data, catalog), model_label or provider.model_label


# Validate the provider's JSON into a RedTeamResult, enforcing the voice
# constraint: drop fabricated warning-sign ids and enrich real ones with
# their canonical name + bucket from the catalog.
def _build_result(data: dict, catalog: Catalog) -> RedTeamResult:
    enriched: list[dict] = []
    catalog_by_id = by_id(catalog)
    for m_raw in data.get("matched_warning_signs", []):
        wid = m_raw.get("id")
        if not wid or wid not in catalog_by_id:
            # Model fabricated an id — drop it (cite real ids only).
            continue
        ws = catalog_by_id[wid]
        enriched.append({
            "id": wid,
            "name": ws.name,
            "bucket_id": ws.buckets[0],
            "fit_strength": float(m_raw.get("fit_strength", 0.5)),
        })
    data["matched_warning_signs"] = [MatchedWarningSign.model_validate(m) for m in enriched]
    return RedTeamResult.model_validate(data)

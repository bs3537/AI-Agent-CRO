"""Anthropic SDK wrapper for the red team.

Same model class as the scorer (Sonnet 4.6 for throughput). System prompt
includes the full catalog and is marked cache_control=ephemeral so repeated
calls within 5 minutes hit the cache.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .catalog import Catalog, by_id
from .prompt import build_system_prompt, build_user_message
from .schema import MatchedWarningSign, RedTeamCandidate, RedTeamResult

# Same Sonnet 4.6 as the scorer — Opus is reserved for the digest narrative.
DEFAULT_MODEL = "claude-sonnet-4-6"
# Slightly larger budget than the scorer to fit the bearish_thesis + matches.
MAX_TOKENS = 800


# Exception raised for any red-team Claude call failure. Caller catches
# to log + dead-letter the score row.
class RedTeamClaudeError(RuntimeError):
    pass


# Run one red-team pass via Claude. Returns (result, model_used) and
# records token usage to the Phase 6 cost ledger as a side effect.
def red_team_with_claude(
    candidate: RedTeamCandidate,
    catalog: Catalog,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    client: Any | None = None,
) -> tuple[RedTeamResult, str]:
    if client is None:
        try:
            import anthropic
        except ImportError as e:
            raise RedTeamClaudeError("anthropic SDK not installed") from e
        client = anthropic.Anthropic(api_key=api_key)

    system_prompt = build_system_prompt(catalog)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": build_user_message(candidate)}],
        )
    except Exception as e:
        raise RedTeamClaudeError(f"Claude red-team call failed: {e}") from e

    # Phase 6: record token usage for the cost ledger / degrade cascade.
    try:
        from ..orchestrator.cost import record_usage
        record_usage(kind="red_team", model=model, usage=getattr(resp, "usage", None),
                     related_event_id=candidate.article_event_id)
    except Exception:
        pass

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    return _parse_result(text, catalog), model


# Parse the model's JSON response and enforce voice constraints: drop any
# matched_warning_signs whose id isn't in the catalog (fabricated ids),
# enrich the rest with their canonical name + bucket from the catalog.
def _parse_result(text: str, catalog: Catalog) -> RedTeamResult:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate)
    m = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not m:
        raise RedTeamClaudeError(f"No JSON in red-team response: {text[:300]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise RedTeamClaudeError(f"Could not parse red-team JSON: {e}") from e

    # Enrich matched_warning_signs with catalog name/bucket
    enriched: list[dict] = []
    catalog_by_id = by_id(catalog)
    for m_raw in data.get("matched_warning_signs", []):
        wid = m_raw.get("id")
        if not wid or wid not in catalog_by_id:
            # Model fabricated an id — drop it. Voice constraints say cite real ids only.
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

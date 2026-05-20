"""Anthropic SDK wrapper for severity scoring.

Sonnet for throughput (PLAN §3). Opus is reserved for the Phase 5 evening
digest synthesis. The system prompt is marked for ephemeral caching so
repeated scoring calls hit the cache within its 5-min TTL.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .prompt import SYSTEM_PROMPT, build_user_message
from .schema import AxisScores, ScoreCandidate

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 512


class ClaudeError(RuntimeError):
    pass


def score_with_claude(
    candidate: ScoreCandidate,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    client: Any | None = None,
) -> tuple[AxisScores, str]:
    """Returns (axis_scores, model_used_str)."""
    if client is None:
        try:
            import anthropic
        except ImportError as e:
            raise ClaudeError(
                "anthropic SDK not installed. Run: uv pip install anthropic"
            ) from e
        client = anthropic.Anthropic(api_key=api_key)

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": build_user_message(candidate)}],
        )
    except Exception as e:
        raise ClaudeError(f"Claude API call failed: {e}") from e

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    return _parse_json(text), model


def _parse_json(text: str) -> AxisScores:
    """Extract the JSON object from the response and validate it.

    Sonnet 4.x reliably emits JSON when instructed, but sometimes wraps it in
    a markdown fence or adds a stray prefix. Strip those before parsing.
    """
    candidate = text.strip()
    # Strip markdown fence if present
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate)
    # Find the outermost JSON object
    m = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not m:
        raise ClaudeError(f"No JSON object found in response: {text[:300]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ClaudeError(f"Could not parse JSON: {e}; body={m.group(0)[:300]}") from e
    return AxisScores.model_validate(data)

"""Surface missed-events as warning-signs library candidates (PLAN.MD §7).

Every "I wish I'd noticed earlier" entered via
`python -m sma_monitor.outputs mark-missed` becomes a candidate here.

The emitted YAML is a *draft* — the user must add keywords + a real
historical_example + an invalidator before pasting into `catalog.yaml` and
bumping `catalog_version`.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from ..outputs.store import list_missed


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:max_len] or "candidate"


def library_growth_candidates(*, days: int = 90) -> list[dict]:
    """Missed events within window, in order of recording (oldest first)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = list_missed(limit=200)
    out: list[dict] = []
    for r in rows:
        try:
            recorded = datetime.fromisoformat(r["recorded_at"])
        except ValueError:
            continue
        if recorded < cutoff:
            continue
        slug = _slugify(r["description"])
        out.append({
            "id": f"candidate_{slug}",
            "ticker": r["ticker"],
            "bucket_id_guess": r["bucket_id_guess"],
            "description": r["description"],
            "article_url": r["article_url"],
            "note": r["note"],
            "recorded_at": r["recorded_at"],
        })
    return out


def render_candidate_yaml(c: dict) -> str:
    """Draft warning-sign YAML block — user must complete keywords + invalidator."""
    bucket = c["bucket_id_guess"] if c["bucket_id_guess"] is not None else "?  # FILL IN"
    ticker_line = f"  # ticker context: {c['ticker']}\n" if c["ticker"] else ""
    url_line = f"    Article: {c['article_url']}\n    " if c["article_url"] else "    "
    note_line = f"User note: {c['note']}\n    " if c["note"] else ""
    return (
        f"- id: {c['id']}\n"
        f"{ticker_line}"
        f"  name: \"{c['description'][:80]}\"\n"
        f"  buckets: [{bucket}]\n"
        f"  definition: |\n"
        f"    {c['description']}\n"
        f"  keywords:\n"
        f"    - \"TODO: 3-5 distinctive phrases that match this pattern\"\n"
        f"  historical_example: |\n"
        f"    {url_line}{note_line}"
        f"Recorded as missed by user on {c['recorded_at'][:10]}.\n"
        f"  invalidator: \"TODO: one sentence — what would invalidate this pattern\"\n"
    )


def render_candidates_markdown(candidates: Sequence[dict]) -> str:
    if not candidates:
        return ("*No missed events recorded — nothing to add to the library. "
                "Use `python -m sma_monitor.outputs mark-missed` to record "
                'events the agent should have surfaced.*')
    parts = [
        f"*{len(candidates)} missed event(s) → candidate warning-sign blocks below. "
        f"Each is a DRAFT — fill in `keywords` and `invalidator` before pasting "
        f"into `data/warning_signs/catalog.yaml`. Bump `catalog_version` after.*",
        "",
        "```yaml",
    ]
    for c in candidates:
        parts.append(render_candidate_yaml(c))
    parts.append("```")
    return "\n".join(parts)

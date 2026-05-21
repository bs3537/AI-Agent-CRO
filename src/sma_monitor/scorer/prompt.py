"""Build Claude prompts for severity scoring.

System message (cacheable): role + rubric + output format.
User message (per article): holding + bucket + article text.

The system message is constant per MULTIPLIERS_VERSION + rubric content, so
we mark it with cache_control=ephemeral. Claude caches it for 5min, which
lines up with how often the scorer batches articles.
"""
from __future__ import annotations

from .rubric import RUBRIC_TEXT
from .schema import ScoreCandidate

# System prompt: role declaration + full rubric + output schema. Cached
# at the API level (ephemeral, 5-min TTL) since it's constant across calls.
SYSTEM_PROMPT = f"""\
You are a neutral severity scorer for a biotech-heavy SMA news monitor. \
You score one news article for one position along three axes. \
You DO NOT recommend actions — no "buy"/"sell"/"trim"/"add" verbs. \
You produce structured JSON only.

{RUBRIC_TEXT}"""


# Build the per-candidate user message: holding context + article excerpt.
# Compact, label-prefixed format so Claude can parse it deterministically
# and so the prompt stays well under typical context budgets.
def build_user_message(c: ScoreCandidate) -> str:
    nearest = (
        f"{c.nearest_catalyst_days}d ({'near' if c.nearest_catalyst_days <= 14 else 'far'})"
        if c.nearest_catalyst_days is not None
        else "no catalyst on file"
    )
    secondary = (
        ", ".join(f"#{bid} (conf {conf:.2f})" for bid, conf in c.secondary_buckets[:3])
        if c.secondary_buckets
        else "—"
    )
    src_line = (
        f"{c.source} (source tier {c.source_tier})"
        if c.source and c.source_tier
        else (c.source or "—")
    )
    published = c.published_at.isoformat() if c.published_at else "—"

    return f"""\
HOLDING
  Ticker:           {c.ticker}
  % NAV:            {c.pct_nav * 100:.2f}%
  Conviction tier:  {c.conviction_tier} (1=watchlist, 5=core)
  Stage:            {c.stage}
  Nearest catalyst: {nearest}
  Thesis: {c.thesis.strip()}

ARTICLE BUCKET
  Primary:   #{c.primary_bucket_id} {c.primary_bucket_name} (tagger conf {c.primary_bucket_confidence:.2f})
  Secondary: {secondary}

ARTICLE
  Source:    {src_line}
  Published: {published}
  Title:     {c.title}
  Excerpt:   {c.excerpt.strip()}

Score this article's severity for THIS holding. Output JSON only — no \
preamble, no markdown fences.
"""

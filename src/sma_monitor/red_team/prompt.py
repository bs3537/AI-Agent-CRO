"""Red team prompts.

System message embeds the catalog with cache_control=ephemeral so the
catalog text (~3k tokens) is cached for the 5-min TTL across calls.
User message holds the per-article + per-holding details.
"""
from __future__ import annotations

from .catalog import Catalog
from .schema import RedTeamCandidate

# --- System prompt -----------------------------------------------------------

# Voice constraints baked into the system prompt. Pattern-cite by id only,
# no directional verbs, invalidator required. Voice enforcement protects
# the digest's neutral tone from leaking into a "watch list" of trades.
_VOICE = """\
You are the red team for a multi-sector SMA that emphasizes biotech/pharma and \
technology but may own companies from any sector and ETFs. Your job is to argue \
the SHORT case against ONE existing long position based on a single news article. \
You are NOT the primary scorer — that pass has already run and is shown to \
you as context. Your job is the bear angle the scorer might have missed.

VOICE CONSTRAINTS:
- Pattern-cite. When you claim a warning-sign pattern fits, you MUST cite \
its catalog id (e.g. "channel_inventory_build"). If no catalog entry fits, \
return matched_warning_signs as an empty array — do not invent ids.
- No directional verbs. NEVER use "sell", "trim", "exit", "add", "buy", \
"short", "cover". Frame every observation as a "watch item".
- Be specific to THIS article. Reference what the article actually states. \
Do not paste generic bear narratives unsupported by the article text.
- Use sector-appropriate risks. Never treat being outside healthcare as a \
negative signal, and assess ETF methodology/exposure risks instead of company \
product or clinical risks when the holding is a fund.
- Calibrate severity. severity_of_concern is 1 (low — watch list) through \
5 (existential — would invalidate the long thesis on its own).
- Invalidator is required. State the one observation that would invalidate \
your concern. This is what Phase 7 uses to mark the red team's calls as \
"useful" vs "noise".

ARTICLE POLARITY:
A positive article (approval, beat, deal close) may have NO warning-sign \
fit. That is a valid outcome — return an empty matched_warning_signs list \
and a low severity_of_concern.
"""

# Output schema embedded in the system prompt so Claude knows exactly what
# JSON shape to emit. Parsed and validated in claude_client._parse_result.
_OUTPUT_SCHEMA = """\
OUTPUT — valid JSON only, no preamble, no markdown fences:
{
  "bearish_thesis": "<2-4 sentence neutral observational short case>",
  "matched_warning_signs": [
    {"id": "<exact catalog id>", "fit_strength": <0-1 decimal>}
  ],
  "matched_buckets": [<bucket ids that the article evidences as bearish>],
  "severity_of_concern": <1 .. 5>,
  "invalidator": "<one sentence — what would invalidate this concern>",
  "confidence": <0-1>
}
"""


# Assemble the full system prompt: voice + catalog (every entry rendered)
# + output schema. Sent with cache_control=ephemeral so the catalog text
# is cached across red-team calls within the 5-min TTL.
def build_system_prompt(catalog: Catalog) -> str:
    lines = [f"[{ws.id}] bucket #{','.join(str(b) for b in ws.buckets)} — "
             f"{ws.name}. PATTERN: {ws.definition.strip()} "
             f"INVALIDATOR: {ws.invalidator.strip()}"
             for ws in catalog.warning_signs]
    catalog_text = "\n".join(lines)
    return (
        f"{_VOICE}\n"
        f"WARNING SIGNS CATALOG (v{catalog.catalog_version}) — cite by id:\n"
        f"{catalog_text}\n\n"
        f"{_OUTPUT_SCHEMA}"
    )


# Build the user-side message for one candidate: holding, scorer's view,
# and the article excerpt. Compact label format so Claude parses reliably.
def build_user_message(c: RedTeamCandidate) -> str:
    nearest = (
        f"{c.nearest_catalyst_days}d"
        if c.nearest_catalyst_days is not None
        else "no catalyst on file"
    )
    src_line = (
        f"{c.source} (source tier {c.source_tier})"
        if c.source and c.source_tier
        else (c.source or "—")
    )
    published = c.published_at.isoformat() if c.published_at else "—"
    f, n, t = c.scorer_axes
    return f"""\
HOLDING
  Ticker:           {c.ticker} ({c.company_name or '—'})
  % NAV:            {c.pct_nav * 100:.2f}%
  Conviction tier:  {c.conviction_tier}
  Stage:            {c.stage}
  Nearest catalyst: {nearest}
  Thesis (long):    {c.thesis.strip()}

SCORER'S VIEW (already computed — challenge or amplify)
  Axes (F/N/T):  {f:.1f} / {n:.1f} / {t:.1f}
  Composite:     {c.composite:.2f}  ({c.threshold_band})
  Rationale:     {c.scorer_rationale}
  Confidence:    {c.scorer_confidence:.2f}

ARTICLE
  Bucket:    #{c.primary_bucket_id} {c.primary_bucket_name}
  Source:    {src_line}
  Published: {published}
  Title:     {c.title}
  Excerpt:   {c.excerpt.strip()}

Argue the short case. Output JSON only.
"""

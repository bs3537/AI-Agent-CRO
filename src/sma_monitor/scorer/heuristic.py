"""Deterministic offline scorer.

Two roles:
  1. Phase 6 cost-degrade fallback when the token budget is exhausted.
  2. Demo / calibration without an Anthropic key.

Heuristic is intentionally conservative — it floors at moderate scores and
only escalates when there are strong textual signals. The Claude scorer
provides the nuance; this just keeps the pipeline producing usable output
when Claude is unavailable.
"""
from __future__ import annotations

from .schema import AxisScores, ScoreCandidate

# Label persisted in scores.model_used so heuristic-scored rows are
# distinguishable from Claude-scored rows in audits.
MODEL_LABEL = "heuristic-v1"

# Substrings that strongly imply high financial impact. Lowercase only —
# matched via `in` against the lowercased title+excerpt.
_HIGH_SEVERITY_SIGNALS = (
    "complete response letter",
    " crl ",
    "going concern",
    "clinical hold",
    "warning letter",
    "drug shortage",
    "material weakness",
    "restatement",
    "doj investigation",
    "sec investigation",
    "subpoena",
    "going-concern",
)
# Substrings that imply imminent time pressure. Catalyst-adjacent vocabulary.
_TIME_CRITICAL_SIGNALS = (
    "pdufa",
    "adcom",
    "topline",
    "phase 3 readout",
    "interim analysis",
    "definitive agreement",
    "merger agreement",
)

# Per-bucket narrative ceilings. Flow data (#12) can't shift a thesis as
# hard as a clinical readout (#1), so cap it lower.
_NARRATIVE_CAP: dict[int, float] = {
    1: 9.0, 2: 9.0, 3: 7.0, 4: 8.0, 5: 7.0, 6: 6.0, 7: 8.0, 8: 6.0,
    9: 8.0, 10: 6.0, 11: 5.0, 12: 4.0,
}


# Deterministic offline scorer. Conservative baseline + bumps for strong
# textual signals. Used in --offline mode and as the Phase 6 cost-degrade
# fallback when the daily budget is exhausted.
def score_heuristically(c: ScoreCandidate) -> AxisScores:
    text = f"{c.title} {c.excerpt}".lower()

    # financial_impact — baseline + bucket tagger confidence + source quality + signals
    fi = 4.0
    fi += min(c.primary_bucket_confidence, 1.0) * 2.0
    if c.source_tier is not None:
        if c.source_tier <= 2:
            fi += 1.5
        elif c.source_tier == 3:
            fi += 0.5
        elif c.source_tier >= 6:
            fi -= 1.0
    if any(s in text for s in _HIGH_SEVERITY_SIGNALS):
        fi += 2.0
    fi = max(0.0, min(fi, 10.0))

    # narrative_shift — bucket-aware ceiling, scaled by tagger confidence
    ns = 3.5 + min(c.primary_bucket_confidence, 1.0) * 4.0
    ns = min(ns, _NARRATIVE_CAP.get(c.primary_bucket_id, 7.0))

    # time_criticality — catalyst-aware + signal keywords
    tc = 3.0
    if c.nearest_catalyst_days is not None and c.primary_bucket_id in (1, 2):
        if c.nearest_catalyst_days <= 3:
            tc = 8.0
        elif c.nearest_catalyst_days <= 14:
            tc = 6.0
        elif c.nearest_catalyst_days <= 60:
            tc = 4.5
    if any(s in text for s in _TIME_CRITICAL_SIGNALS):
        tc = max(tc, 7.0)

    # confidence — penalized when text is thin or bucket conf is weak
    conf = 0.5
    if len(c.excerpt) > 200:
        conf += 0.15
    conf += min(c.primary_bucket_confidence, 1.0) * 0.25
    if c.source_tier is not None and c.source_tier <= 3:
        conf += 0.1
    conf = max(0.1, min(conf, 0.85))  # heuristic never claims >0.85 confidence

    return AxisScores(
        financial_impact=round(fi, 2),
        narrative_shift=round(ns, 2),
        time_criticality=round(tc, 2),
        rationale=f"Heuristic baseline (bucket #{c.primary_bucket_id}, tagger conf {c.primary_bucket_confidence:.2f}, src tier {c.source_tier or '-'}).",
        confidence=round(conf, 2),
    )

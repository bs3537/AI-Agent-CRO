"""Deterministic offline red team.

Keyword-overlap against the warning-signs catalog. Two roles:
  1. Phase 6 cost-degrade fallback when the token budget is exhausted.
  2. Demos / calibration without an Anthropic key.

The heuristic CANNOT distinguish article polarity (positive vs negative
events using the same vocabulary). It will sometimes match a positive
article on a bearish pattern. Tag these as low fit_strength and let the
human reader override. Live Claude red-team handles polarity correctly.
"""
from __future__ import annotations

from .catalog import Catalog
from .schema import MatchedWarningSign, RedTeamCandidate, RedTeamResult

MODEL_LABEL = "heuristic-v1"
_MIN_FIT = 0.3


def red_team_heuristically(
    c: RedTeamCandidate,
    catalog: Catalog,
) -> RedTeamResult:
    text = (c.title + " " + c.excerpt).lower()

    matched: list[MatchedWarningSign] = []
    for ws in catalog.warning_signs:
        if not ws.keywords:
            continue
        hits = sum(1 for k in ws.keywords if k.lower() in text)
        if hits == 0:
            continue
        denom = max(min(3, len(ws.keywords)), 1)
        fit = min(hits / denom, 1.0)
        if fit < _MIN_FIT:
            continue
        matched.append(MatchedWarningSign(
            id=ws.id,
            name=ws.name,
            bucket_id=ws.buckets[0],
            fit_strength=round(fit, 3),
        ))
    matched.sort(key=lambda m: m.fit_strength, reverse=True)

    # Severity: 1 if no match; otherwise scaled from max fit + composite band
    if not matched:
        severity = 1
    else:
        top = matched[0].fit_strength
        severity = 2 + round(top * 2)
        if c.threshold_band == "above_t":
            severity = max(severity, 4)
        severity = max(1, min(5, severity))

    if matched:
        cited = ", ".join(m.id for m in matched[:3])
        thesis = (
            f"Article keyword-matches {len(matched)} catalog pattern(s) — top: {cited}. "
            f"For {c.ticker} (tier {c.conviction_tier}, {c.stage}), this is a watch item "
            f"that pressures the long thesis as stated."
        )
    else:
        thesis = (
            f"No catalog pattern fits this article. Scorer flagged composite={c.composite:.2f} "
            f"({c.threshold_band}), but the red team finds no specific bearish pattern in the "
            f"published warning-signs library — possible library gap (Phase 7 candidate)."
        )

    matched_buckets = sorted({m.bucket_id for m in matched}) or [c.primary_bucket_id]

    invalidator = (
        "Subsequent disclosure or reporting that contradicts the article's stated facts."
        if matched
        else "Pattern-by-pattern review of the catalog confirms no match (i.e. genuine gap, not coverage miss)."
    )

    # Heuristic never claims >0.75 confidence
    confidence = round(0.55 + (0.15 if matched else 0.0), 2)

    return RedTeamResult(
        bearish_thesis=thesis,
        matched_warning_signs=matched,
        matched_buckets=matched_buckets,
        severity_of_concern=severity,
        invalidator=invalidator,
        confidence=confidence,
    )

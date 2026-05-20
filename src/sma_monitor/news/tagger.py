"""Keyword-based bucket tagger + ticker matcher.

Deterministic floor for Phase 2. Phase 3 may layer a Claude classifier on top
for low-confidence cases — but the keyword tagger always runs first because
it's cheap, repeatable, and Phase 6 cost-degradation needs to be able to
fall back to it.

Multi-bucket articles allowed and expected (PLAN.MD §2C).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .buckets import Bucket

MIN_CONFIDENCE = 0.15
_DENOM_CAP = 5  # Confidence saturates after 5 unique term hits.


@dataclass(frozen=True)
class BucketTag:
    bucket_id: int
    confidence: float


def tag_text(text: str, buckets: dict[int, Bucket]) -> list[BucketTag]:
    """Return tags sorted by descending confidence."""
    lowered = (text or "").lower()
    if not lowered:
        return []
    tags: list[BucketTag] = []
    for b in buckets.values():
        if not b.search_terms:
            continue
        hits = sum(1 for term in b.search_terms if term.lower() in lowered)
        if hits == 0:
            continue
        denom = max(min(_DENOM_CAP, len(b.search_terms)), 1)
        conf = min(hits / denom, 1.0)
        if conf >= MIN_CONFIDENCE:
            tags.append(BucketTag(bucket_id=b.id, confidence=round(conf, 3)))
    tags.sort(key=lambda t: t.confidence, reverse=True)
    return tags


def match_tickers(text: str, holdings_entities: dict[str, list[str]]) -> list[str]:
    """Which holdings does the article mention?

    Ticker matches require word boundaries (MRNA != "merna"). Alias/brand/
    product matches are case-insensitive substrings — those identifiers are
    distinctive enough that substring is fine.
    """
    lowered = (text or "").lower()
    if not lowered:
        return []
    out: list[str] = []
    for ticker, terms in holdings_entities.items():
        if re.search(rf"\b{re.escape(ticker.lower())}\b", lowered):
            out.append(ticker)
            continue
        for term in terms:
            if not term or term.upper() == ticker.upper():
                continue
            if term.lower() in lowered:
                out.append(ticker)
                break
    return out

"""Calibration harness (PLAN.MD §3).

Loads data/scores/calibration_set.yaml, scores each entry, and reports any
mismatch against the user's post-hoc expected_band. Use before going live
and after every meaningful multiplier change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from ..paths import DATA_ROOT
from ..news.buckets import load_buckets
from .heuristic import MODEL_LABEL as HEURISTIC_MODEL, score_heuristically
from .claude_client import ClaudeError, DEFAULT_MODEL, score_with_claude
from .multipliers import (
    BUCKET_WEIGHTS, CONVICTION_MULT, MULTIPLIERS_VERSION,
    catalyst_boost, position_weight, stage_interaction, threshold_band,
)
from .schema import ScoreCandidate

CALIBRATION_FILE = DATA_ROOT / "scores" / "calibration_set.yaml"

ExpectedBand = Literal["above_t", "t2_to_t", "below_t2"]


class CalibrationContext(BaseModel):
    pct_nav: float
    conviction_tier: int
    stage: str
    thesis: str = ""
    nearest_catalyst_days: int | None = None


class CalibrationArticle(BaseModel):
    title: str
    excerpt: str
    source: str | None = None
    source_tier: int | None = None


class CalibrationEntry(BaseModel):
    name: str
    ticker: str
    article: CalibrationArticle
    primary_bucket_id: int
    primary_bucket_confidence: float = 0.7
    context: CalibrationContext
    expected_band: ExpectedBand
    note: str | None = None


class CalibrationSet(BaseModel):
    entries: list[CalibrationEntry]


def load_set(path: Path = CALIBRATION_FILE) -> CalibrationSet:
    if not path.exists():
        return CalibrationSet(entries=[])
    with path.open() as f:
        return CalibrationSet.model_validate(yaml.safe_load(f))


def run(*, api_key: str | None, offline: bool = False) -> list[dict]:
    cset = load_set()
    if not cset.entries:
        return []
    buckets = load_buckets()
    out: list[dict] = []
    for entry in cset.entries:
        bucket = buckets[entry.primary_bucket_id]
        candidate = ScoreCandidate(
            article_event_id=f"calibration:{entry.name}",
            title=entry.article.title,
            excerpt=entry.article.excerpt,
            source=entry.article.source,
            source_tier=entry.article.source_tier,
            published_at=datetime.now(timezone.utc),
            ticker=entry.ticker.upper(),
            pct_nav=entry.context.pct_nav,
            conviction_tier=entry.context.conviction_tier,
            stage=entry.context.stage,
            thesis=entry.context.thesis or "(no thesis provided)",
            nearest_catalyst_days=entry.context.nearest_catalyst_days,
            primary_bucket_id=entry.primary_bucket_id,
            primary_bucket_name=bucket.name,
            primary_bucket_confidence=entry.primary_bucket_confidence,
        )

        if offline or not api_key:
            axes = score_heuristically(candidate)
            model_used = HEURISTIC_MODEL
        else:
            try:
                axes, model_used = score_with_claude(candidate, api_key=api_key)
            except ClaudeError as e:
                out.append({"name": entry.name, "status": "error", "err": str(e)})
                continue

        raw_avg = (axes.financial_impact + axes.narrative_shift + axes.time_criticality) / 3
        composite = (
            raw_avg
            * BUCKET_WEIGHTS.get(entry.primary_bucket_id, 0.7)
            * position_weight(entry.context.pct_nav)
            * CONVICTION_MULT.get(entry.context.conviction_tier, 1.0)
            * catalyst_boost(entry.context.nearest_catalyst_days, entry.primary_bucket_id)
            * stage_interaction(entry.context.stage, entry.primary_bucket_id)
        )
        band = threshold_band(composite)
        match = band == entry.expected_band

        out.append({
            "name": entry.name,
            "ticker": entry.ticker,
            "bucket_id": entry.primary_bucket_id,
            "axes": (
                round(axes.financial_impact, 2),
                round(axes.narrative_shift, 2),
                round(axes.time_criticality, 2),
            ),
            "composite": round(composite, 2),
            "band": band,
            "expected_band": entry.expected_band,
            "match": match,
            "model": model_used,
            "version": MULTIPLIERS_VERSION,
            "rationale": axes.rationale,
        })
    return out

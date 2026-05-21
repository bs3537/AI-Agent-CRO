"""Orchestrate scoring: pick unscored pairs → score → compose → persist.

Per PLAN.MD §3: scorer is the neutral first pass. Phase 4 red team runs on
articles in (T₂, T] and above. Phase 5 alerts trigger at composite ≥ T.

Failure handling: per-pair errors are logged and the pair stays unscored.
Don't crash the batch.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from ..news.buckets import load_buckets
from .claude_client import ClaudeError, DEFAULT_MODEL, score_with_claude
from .heuristic import MODEL_LABEL as HEURISTIC_MODEL, score_heuristically
from .multipliers import (
    BUCKET_WEIGHTS,
    CONVICTION_MULT,
    MULTIPLIERS_VERSION,
    catalyst_boost,
    position_weight,
    stage_interaction,
    threshold_band,
)
from .schema import AxisScores, CompositeScore, ScoreCandidate
from .store import (
    init_scores_schema,
    inputs_hash,
    save_score,
    secondary_buckets_for,
    unscored_pairs,
)

log = logging.getLogger("sma_monitor.scorer.pipeline")

Scorer = Callable[[ScoreCandidate], tuple[AxisScores, str]]


def score_unscored(
    *,
    api_key: str | None,
    offline: bool = False,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
) -> dict:
    """Score every (article, ticker) pair lacking a score at MULTIPLIERS_VERSION."""
    init_scores_schema()
    holdings, _missing, _ = latest_joined()
    holdings_by_ticker: dict[str, Holding] = {h.ticker: h for h in holdings}
    if not holdings_by_ticker:
        log.warning("no_holdings_loaded")
        return {"scored": 0, "errors": 0, "skipped": 0}
    buckets = load_buckets()

    if offline or not api_key:
        scorer: Scorer = lambda c: (score_heuristically(c), HEURISTIC_MODEL)
        scorer_label = HEURISTIC_MODEL
    else:
        scorer = lambda c: score_with_claude(c, api_key=api_key, model=model)
        scorer_label = model

    rows = unscored_pairs(MULTIPLIERS_VERSION, limit=limit)
    scored = errors = skipped = 0
    for row in rows:
        ticker = row["ticker"]
        holding = holdings_by_ticker.get(ticker)
        if holding is None:
            # Article tagged to a ticker we no longer hold — skip.
            skipped += 1
            continue
        primary_bid = row["primary_bucket_id"]
        if primary_bid is None or primary_bid not in buckets:
            skipped += 1
            continue
        bucket = buckets[primary_bid]
        secondaries = [
            (r["bucket_id"], r["confidence"])
            for r in secondary_buckets_for(row["article_event_id"], primary_bid)
        ]
        candidate = ScoreCandidate(
            article_event_id=row["article_event_id"],
            title=row["title"] or "",
            excerpt=row["excerpt"] or "",
            source=row["source"],
            source_tier=row["source_tier"],
            published_at=_parse_dt(row["published_at"]),
            ticker=ticker,
            pct_nav=holding.pct_nav,
            conviction_tier=int(holding.conviction_tier),
            stage=holding.stage,
            thesis=holding.thesis,
            nearest_catalyst_days=holding.nearest_catalyst_days,
            primary_bucket_id=primary_bid,
            primary_bucket_name=bucket.name,
            primary_bucket_confidence=float(row["primary_bucket_confidence"] or 0.0),
            secondary_buckets=secondaries,
        )
        try:
            axes, model_used = scorer(candidate)
        except (ClaudeError, ValueError) as e:
            try:
                from ..orchestrator.dead_letter import record_failure
                record_failure(
                    kind="score",
                    article_event_id=candidate.article_event_id,
                    ticker=ticker, error=str(e)[:500],
                )
            except Exception:
                pass  # dead-letter recording must not break scoring
            log.error(
                "scorer_failed",
                extra={"article_event_id": candidate.article_event_id,
                       "ticker": ticker, "err": str(e)},
            )
            errors += 1
            continue
        # Successful (re)try — clear any dead-letter row for this pair.
        try:
            from ..orchestrator.dead_letter import clear_on_success
            from ..orchestrator.store import dead_letter_event_id
            clear_on_success(dead_letter_event_id("score", candidate.article_event_id, ticker))
        except Exception:
            pass

        composite = _compose(axes, candidate)
        save_score(_to_row(candidate, axes, composite, model_used))
        scored += 1
        log.info(
            "scored",
            extra={
                "ticker": ticker,
                "bucket": primary_bid,
                "composite": composite["composite"],
                "band": composite["band"],
                "title": candidate.title[:80],
            },
        )

    log.info("scoring_done",
             extra={"scored": scored, "errors": errors, "skipped": skipped,
                    "model": scorer_label, "version": MULTIPLIERS_VERSION})
    return {"scored": scored, "errors": errors, "skipped": skipped}


def _compose(axes: AxisScores, c: ScoreCandidate) -> dict:
    raw_avg = (axes.financial_impact + axes.narrative_shift + axes.time_criticality) / 3
    bw = BUCKET_WEIGHTS.get(c.primary_bucket_id, 0.7)
    pw = position_weight(c.pct_nav)
    cm = CONVICTION_MULT.get(c.conviction_tier, 1.0)
    cb = catalyst_boost(c.nearest_catalyst_days, c.primary_bucket_id)
    si = stage_interaction(c.stage, c.primary_bucket_id)
    comp = raw_avg * bw * pw * cm * cb * si
    return {
        "raw_avg": round(raw_avg, 3),
        "bucket_weight": bw,
        "position_weight": pw,
        "conviction_mult": cm,
        "catalyst_boost": cb,
        "stage_interaction": si,
        "composite": round(comp, 3),
        "band": threshold_band(comp),
    }


def _to_row(
    c: ScoreCandidate,
    axes: AxisScores,
    composite: dict,
    model_used: str,
) -> CompositeScore:
    ih = inputs_hash(
        article_event_id=c.article_event_id,
        ticker=c.ticker,
        primary_bucket_id=c.primary_bucket_id,
        pct_nav=c.pct_nav,
        conviction_tier=c.conviction_tier,
        stage=c.stage,
        nearest_catalyst_days=c.nearest_catalyst_days,
        multipliers_version=MULTIPLIERS_VERSION,
    )
    return CompositeScore(
        article_event_id=c.article_event_id,
        ticker=c.ticker,
        primary_bucket_id=c.primary_bucket_id,
        secondary_buckets=c.secondary_buckets,
        financial_impact=axes.financial_impact,
        narrative_shift=axes.narrative_shift,
        time_criticality=axes.time_criticality,
        raw_avg=composite["raw_avg"],
        bucket_weight=composite["bucket_weight"],
        position_weight=composite["position_weight"],
        conviction_mult=composite["conviction_mult"],
        catalyst_boost=composite["catalyst_boost"],
        stage_interaction=composite["stage_interaction"],
        composite=composite["composite"],
        threshold_band=composite["band"],
        rationale=axes.rationale,
        confidence=axes.confidence,
        model_used=model_used,
        multipliers_version=MULTIPLIERS_VERSION,
        inputs_hash=ih,
        scored_at=datetime.now(timezone.utc),
    )


def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

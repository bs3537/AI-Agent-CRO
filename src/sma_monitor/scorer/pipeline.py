"""Orchestrate scoring: pick unscored pairs → score → compose → persist.

Per PLAN.MD §3: scorer is the neutral first pass. Phase 4 red team runs on
articles in (T₂, T] and above. Phase 5 alerts trigger at composite ≥ T.

Failure handling: per-pair errors are logged and the pair stays unscored.
Don't crash the batch.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from ..llm import get_provider
from ..llm.throughput import llm_concurrency, map_concurrent
from ..news.buckets import load_buckets
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from .claude_client import DEFAULT_MODEL, score_with_llm
from .heuristic import MODEL_LABEL as HEURISTIC_MODEL
from .heuristic import score_heuristically
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

# Signature shared by the LLM scorer and the heuristic scorer so the
# pipeline can swap them transparently based on --offline / cost-degrade.
Scorer = Callable[[ScoreCandidate], tuple[AxisScores, str]]


# Score every (article, ticker) pair that lacks a score row at the current
# MULTIPLIERS_VERSION. Routes to the configured LLM provider or heuristic based
# on `offline` and provider availability. Returns a counts summary for logging.
def score_unscored(
    *,
    api_key: str | None,
    offline: bool = False,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
    ticker: str | None = None,
) -> dict:
    """Score every (article, ticker) pair lacking a score at MULTIPLIERS_VERSION."""
    init_scores_schema()
    holdings, _missing, _ = latest_joined()
    holdings_by_ticker: dict[str, Holding] = {h.ticker: h for h in holdings}
    if not holdings_by_ticker:
        log.warning("no_holdings_loaded")
        return {"scored": 0, "errors": 0, "skipped": 0}
    buckets = load_buckets()

    # Use the LLM provider when one is available; otherwise
    # fall back to the deterministic heuristic. `--offline` forces heuristic.
    # `api_key` is retained for signature compatibility but no longer gates.
    # W9: high-volume triage runs on the "scorer" tier for cost tracking.
    provider = get_provider(prefer_offline=offline, stage="scorer")
    if provider is None:
        def scorer(c: ScoreCandidate) -> tuple[AxisScores, str]:
            return score_heuristically(c), HEURISTIC_MODEL

        scorer_label = HEURISTIC_MODEL
    else:
        def scorer(c: ScoreCandidate) -> tuple[AxisScores, str]:
            return score_with_llm(c, provider=provider)

        scorer_label = provider.model_label

    rows = unscored_pairs(MULTIPLIERS_VERSION, limit=limit, ticker=ticker)

    # Phase 1 (sequential reads): build a candidate per scorable pair; tally the
    # pairs we can't score (ticker no longer held, or missing/unknown bucket).
    candidates: list[ScoreCandidate] = []
    skipped = 0
    for row in rows:
        holding = holdings_by_ticker.get(row["ticker"])
        if holding is None:
            skipped += 1  # article tagged to a ticker we no longer hold
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
        candidates.append(ScoreCandidate(
            article_event_id=row["article_event_id"],
            title=row["title"] or "",
            excerpt=row["excerpt"] or "",
            source=row["source"],
            source_tier=row["source_tier"],
            published_at=_parse_dt(row["published_at"]),
            ticker=row["ticker"],
            pct_nav=holding.pct_nav,
            conviction_tier=int(holding.conviction_tier),
            stage=holding.stage,
            thesis=holding.thesis,
            nearest_catalyst_days=holding.nearest_catalyst_days,
            primary_bucket_id=primary_bid,
            primary_bucket_name=bucket.name,
            primary_bucket_confidence=float(row["primary_bucket_confidence"] or 0.0),
            secondary_buckets=secondaries,
        ))

    # Phase 2 (bounded concurrency): score the candidates. Sequential on the
    # heuristic path (workers=1); ~SMA_LLM_CONCURRENCY calls when an LLM
    # provider is active.
    workers = llm_concurrency() if provider is not None else 1
    results = map_concurrent(scorer, candidates, workers=workers)

    # Phase 3 (sequential writes): persist successes, dead-letter failures.
    scored = errors = 0
    for candidate, (res, err) in zip(candidates, results, strict=False):
        if err is not None:
            _record_score_failure(candidate, err)
            errors += 1
            continue
        axes, model_used = res
        _clear_score_dead_letter(candidate)
        composite = _compose(axes, candidate)
        save_score(_to_row(candidate, axes, composite, model_used))
        scored += 1
        log.info(
            "scored",
            extra={
                "ticker": candidate.ticker,
                "bucket": candidate.primary_bucket_id,
                "composite": composite["composite"],
                "band": composite["band"],
                "title": candidate.title[:80],
            },
        )

    log.info("scoring_done",
             extra={"scored": scored, "errors": errors, "skipped": skipped,
                    "model": scorer_label, "version": MULTIPLIERS_VERSION,
                    "ticker": ticker.upper() if ticker else None})
    return {"scored": scored, "errors": errors, "skipped": skipped}


# Record a failed scoring attempt to the dead-letter table (best-effort) and
# log it. Concurrency surfaces every exception type here uniformly, so we
# dead-letter rather than crash the batch (per this module's failure policy).
def _record_score_failure(candidate: ScoreCandidate, err: BaseException) -> None:
    try:
        from ..orchestrator.dead_letter import record_failure
        record_failure(kind="score", article_event_id=candidate.article_event_id,
                        ticker=candidate.ticker, error=str(err)[:500])
    except Exception:
        pass  # dead-letter recording must not break scoring
    log.error("scorer_failed",
              extra={"article_event_id": candidate.article_event_id,
                     "ticker": candidate.ticker, "err": str(err)})


# Clear any dead-letter row for a pair after a successful (re)score.
def _clear_score_dead_letter(candidate: ScoreCandidate) -> None:
    try:
        from ..orchestrator.dead_letter import clear_on_success
        from ..orchestrator.store import dead_letter_event_id
        clear_on_success(
            dead_letter_event_id("score", candidate.article_event_id, candidate.ticker)
        )
    except Exception:
        pass


# Apply the five multipliers + threshold-band classification to the LLM's
# raw axis output. Returns the dict that becomes the persisted score row.
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


# Build the CompositeScore row from the candidate + axes + composite.
# Computes the inputs_hash so the row is keyed on every value that should
# trigger a re-score.
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
        scored_at=datetime.now(UTC),
    )


# Lenient ISO-8601 parser; returns None for missing/unparseable input so
# the caller can keep the field optional.
def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

"""Thesis-drift decision engine (Workstream 3).

For each holding it bundles the long thesis, scored articles, red-team bear
cases, catalysts, and open P&L (a DecisionCandidate), then produces one
HOLD/WATCH/SELL verdict with a short note. Offline-first: a deterministic
heuristic verdict (max red-team severity + composite band) runs whenever no
LLM provider is available; otherwise the configured LLM provider is asked for the
final grade under a strict output schema. Deterministic scores, red-team passes,
FMP metrics, and EMA20 state guide the prompt; when the LLM returns a valid
grade, that grade is authoritative.

Idempotency mirrors the scorer/red-team: inputs_hash captures the thesis plus
the exact evidence set, so run_decisions skips holdings whose thesis and
evidence are unchanged and re-computes the moment either moves.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from ..config import settings
from ..db import init_db
from ..identity import event_id
from ..llm import get_provider
from ..llm.throughput import llm_concurrency, map_concurrent
from ..news.fmp_client import latest_fmp_metrics, latest_price_series
from ..news.store import init_news_schema
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from ..portfolio.uploads import combined_text
from ..red_team.store import init_red_team_schema, recent_passes
from ..scorer.multipliers import T2, T
from ..scorer.store import init_scores_schema, recent_scores
from .prompt import build_system_prompt, build_user_message
from .rating import RATING_VERSION, rate_candidate
from .schema import (
    GRADE_VERDICT,
    VERDICT_COLOR,
    BearEvidence,
    DecisionCandidate,
    PositionDecision,
    PositionRating,
    ScoreEvidence,
)
from .store import (
    has_decision_for,
    has_rating_for,
    init_decision_schema,
    save_decision,
    save_rating,
)
from .technicals import technical_state

log = logging.getLogger("sma_monitor.decision.engine")

# Version string the engine keys idempotency off. Bump when the prompt, the
# heuristic, or the candidate shape changes so every holding re-computes.
DECISION_VERSION = "v1.2-2026.05"

# Label written to position_decisions.model_used for the offline path.
HEURISTIC_MODEL = "heuristic-v1"

# How many of the highest-composite scores / most-severe bears to feed in.
EVIDENCE_LIMIT = 20

# Token ceiling for the LLM verdict call.
MAX_TOKENS = 700

# JSON Schema the provider constrains decision output to. color is derived
# from verdict by the engine, so the model is never asked for it.
DECISION_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "llm_grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "thesis_clause_impacts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "clause_id": {"type": "string"},
                    "impact": {
                        "type": "string",
                        "enum": ["none", "low", "medium", "high", "critical"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["clause_id", "impact", "evidence"],
            },
        },
        "hard_breaker": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "present": {"type": "boolean"},
                "type": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["present", "type", "evidence"],
        },
        "technical_assessment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "uses_ema20": {"type": "boolean"},
                "interpretation": {"type": "string"},
                "should_affect_grade": {
                    "type": "string",
                    "enum": [
                        "none",
                        "cap_A_at_B",
                        "push_one_notch",
                        "no_sell_without_fundamental_confirmation",
                    ],
                },
            },
            "required": ["uses_ema20", "interpretation", "should_affect_grade"],
        },
        "note": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    # OpenAI structured output strict mode expects every declared property to
    # be required when additionalProperties is false. The prompt already asks
    # for all of these fields; keeping schema + prompt aligned avoids Codex
    # rejecting the decision call before the model runs.
    "required": [
        "llm_grade",
        "thesis_clause_impacts",
        "hard_breaker",
        "technical_assessment",
        "note",
        "drivers",
        "confidence",
    ],
}


# Stable hash of the long thesis for one ticker. Changing the thesis text
# changes this hash, which flows into inputs_hash and forces a re-compute.
def thesis_hash(ticker: str, thesis: str) -> str:
    return event_id({"kind": "thesis", "ticker": ticker, "thesis": thesis.strip()})


# Compute the inputs hash that gates re-compute: thesis identity + uploaded
# thesis docs + position size + nearest catalyst + the exact set of
# score/red-team event ids considered + the engine version. Any new scored
# article, red-team pass, thesis edit, or uploaded document changes this hash.
def decision_inputs_hash(
    *,
    ticker: str,
    thesis_h: str,
    pct_nav: float,
    nearest_catalyst_days: int | None,
    score_ids: list[str],
    pass_ids: list[str],
    doc_hash: str = "",
    fmp_hash: str = "",
    technical_hash: str = "",
) -> str:
    return event_id({
        "kind": "decision_inputs",
        "ticker": ticker,
        "thesis_hash": thesis_h,
        "doc_hash": doc_hash,
        "fmp_hash": fmp_hash,
        "technical_hash": technical_hash,
        "pct_nav": round(pct_nav, 4),
        "nearest_catalyst_days": nearest_catalyst_days,
        "score_ids": sorted(score_ids),
        "pass_ids": sorted(pass_ids),
        "decision_version": DECISION_VERSION,
    })


# Assemble a DecisionCandidate for one holding from the joined view plus the
# top scored articles and red-team passes on that ticker.
def build_candidate(
    holding: Holding,
    *,
    evidence_article_event_ids: Sequence[str] | None = None,
) -> DecisionCandidate:
    _init_read_side_schemas()
    scores = [
        _to_score_evidence(r)
        for r in recent_scores(
            ticker=holding.ticker,
            limit=EVIDENCE_LIMIT,
            article_event_ids=evidence_article_event_ids,
        )
    ]
    bears = [
        _to_bear_evidence(r)
        for r in recent_passes(
            ticker=holding.ticker,
            limit=EVIDENCE_LIMIT,
            article_event_ids=evidence_article_event_ids,
        )
    ]

    open_pnl = pnl_pct = None
    if holding.cost_basis is not None:
        open_pnl = holding.market_value - holding.cost_basis
        if holding.cost_basis:
            pnl_pct = open_pnl / holding.cost_basis

    catalysts = [
        f"{c.type} {c.date.isoformat()} ({c.confidence}): {c.description}"
        for c in holding.catalysts
    ]
    max_severity = max((b.severity_of_concern for b in bears), default=1)
    max_composite = max((s.composite for s in scores), default=0.0)

    technical = technical_state(latest_price_series(holding.ticker))

    return DecisionCandidate(
        ticker=holding.ticker,
        company_name=holding.company_name,
        stage=holding.stage,
        conviction_tier=int(holding.conviction_tier),
        thesis=holding.thesis,
        thesis_doc_text=combined_text(holding.ticker),  # W4: uploaded thesis docs
        pct_nav=holding.pct_nav,
        market_value=holding.market_value,
        cost_basis=holding.cost_basis,
        open_pnl=open_pnl,
        pnl_pct=pnl_pct,
        nearest_catalyst_days=holding.nearest_catalyst_days,
        has_overdue_catalyst=holding.has_overdue_catalyst,
        catalysts=catalysts,
        scores=scores,
        bears=bears,
        fmp_metrics=latest_fmp_metrics(holding.ticker),  # W2: FMP financials (None offline)
        technical=technical,
        max_severity=max_severity,
        max_composite=max_composite,
    )


# Project a recent_scores row into the compact ScoreEvidence shape.
def _to_score_evidence(r) -> ScoreEvidence:
    return ScoreEvidence(
        score_event_id=r["event_id"],
        title=r["title"] or "",
        primary_bucket_id=r["primary_bucket_id"],
        composite=r["composite"],
        threshold_band=r["threshold_band"],
        rationale=r["rationale"] or "",
    )


# Project a recent_passes row into the compact BearEvidence shape, parsing the
# matched warning-sign ids out of the stored JSON.
def _to_bear_evidence(r) -> BearEvidence:
    try:
        ws = json.loads(r["matched_warning_signs"] or "[]")
    except json.JSONDecodeError:
        ws = []
    return BearEvidence(
        pass_event_id=r["event_id"],
        title=r["title"] or "",
        bearish_thesis=r["bearish_thesis"] or "",
        severity_of_concern=r["severity_of_concern"],
        matched_patterns=[m.get("id") for m in ws if m.get("id")],
        invalidator=r["invalidator"] or "",
    )


# Decide one holding. Uses the LLM provider when supplied, else the heuristic.
# A valid LLM grade is authoritative; verdict/color are only derived labels.
def decide(
    candidate: DecisionCandidate,
    thesis_h: str,
    inputs_h: str,
    *,
    provider=None,
    compute_source: str = "scheduler",
) -> PositionDecision:
    decision, _llm_grade = decide_with_grade(
        candidate,
        thesis_h,
        inputs_h,
        provider=provider,
        compute_source=compute_source,
    )
    return decision


def decide_with_grade(
    candidate: DecisionCandidate,
    thesis_h: str,
    inputs_h: str,
    *,
    provider=None,
    compute_source: str = "scheduler",
) -> tuple[PositionDecision, str | None]:
    if provider is None:
        verdict, note, drivers, confidence, model_used = _decide_heuristic(candidate)
        llm_grade = None
    else:
        verdict, note, drivers, confidence, model_used, llm_grade = _decide_llm(candidate, provider)

    return PositionDecision(
        ticker=candidate.ticker,
        verdict=verdict,
        color=VERDICT_COLOR[verdict],
        note=note.strip(),
        drivers=drivers[:6],
        confidence=max(0.0, min(1.0, confidence)),
        thesis_hash=thesis_h,
        inputs_hash=inputs_h,
        model_used=model_used,
        compute_source=compute_source,
        decided_at=datetime.now(UTC),
    ), llm_grade


# LLM path: ask the provider for a schema-constrained verdict and record a
# (zero-cost) call to the Phase 6 ledger. Raises LLMError on provider failure;
# run_decisions catches it and the holding is left for the next cycle.
def _decide_llm(candidate: DecisionCandidate, provider):
    data = provider.complete_json(
        system=build_system_prompt(),
        user=build_user_message(candidate),
        schema=DECISION_OUTPUT_SCHEMA,
        max_tokens=MAX_TOKENS,
    )
    try:
        from ..orchestrator.cost import record_llm_call
        record_llm_call(kind="decision", model=provider.model_label)
    except Exception:
        pass
    llm_grade = _llm_grade_from_payload(data)
    if llm_grade is None:
        verdict, note, drivers, confidence, model_used = _decide_heuristic(candidate)
        return verdict, note, drivers, confidence, model_used, None
    verdict = GRADE_VERDICT[llm_grade]
    note = (data.get("note") or "").strip()
    drivers = [str(d).strip() for d in (data.get("drivers") or []) if str(d).strip()]
    confidence = float(data.get("confidence", 0.5))
    return verdict, note, drivers, confidence, provider.model_label, llm_grade


def _llm_grade_from_payload(data: dict) -> str | None:
    grade = data.get("llm_grade")
    if grade in ("A", "B", "C", "D"):
        return grade
    # Backward-compatible adapter for older fake providers or stale Codex output.
    return {"hold": "A", "watch": "C", "sell": "D"}.get(data.get("verdict"))


# Deterministic offline verdict from max red-team severity + composite band.
# Mirrors the red-team heuristic's role: a runnable, demoable decision with no
# model call. Returns (verdict, note, drivers, confidence, model_label).
def _decide_heuristic(c: DecisionCandidate):
    sev, comp = c.max_severity, c.max_composite

    # Verdict ladder. sell needs a severe concern (or a severe-ish concern on
    # an alert-band article); watch covers any real pressure; else hold.
    if sev >= 5 or (sev >= 4 and comp >= T):
        verdict = "sell"
    elif sev >= 3 or comp >= T2 or c.has_overdue_catalyst:
        verdict = "watch"
    else:
        verdict = "hold"

    drivers = _heuristic_drivers(c)
    note = _heuristic_note(c, verdict)
    # Confidence stays modest — the heuristic can't read article polarity.
    confidence = 0.45
    if c.bears:
        confidence += 0.12
    if c.scores:
        confidence += 0.06
    confidence = min(confidence, 0.65)
    return verdict, note, drivers, confidence, HEURISTIC_MODEL


# Build the heuristic driver list: top red-team patterns, the highest-composite
# headline, and a P&L tag — the concrete evidence behind the verdict.
def _heuristic_drivers(c: DecisionCandidate) -> list[str]:
    drivers: list[str] = []
    for b in sorted(c.bears, key=lambda x: x.severity_of_concern, reverse=True)[:3]:
        pats = ", ".join(b.matched_patterns[:2]) if b.matched_patterns else "no catalog pattern"
        drivers.append(f"red-team sev {b.severity_of_concern}/5 ({pats})")
    if c.scores:
        top = max(c.scores, key=lambda s: s.composite)
        drivers.append(
            f"top score {top.composite:.1f} (#{top.primary_bucket_id}): {top.title[:60]}"
        )
    if c.has_overdue_catalyst:
        drivers.append("overdue catalyst on file")
    if c.pnl_pct is not None:
        drivers.append(f"open P&L {c.pnl_pct * 100:+.1f}%")
    return drivers


# Build the heuristic 4–5 line note: thesis restatement, evidence summary,
# position context, verdict rationale, and the invalidator to watch.
def _heuristic_note(c: DecisionCandidate, verdict: str) -> str:
    thesis_line = (c.thesis.strip().replace("\n", " ") or "(no thesis on file)")[:200]
    rationale = {
        "sell": "Verdict SELL: ingested evidence materially contradicts the stated thesis "
                "(severe red-team concern and/or alert-band severity).",
        "watch": "Verdict WATCH: evidence is pressuring the thesis but is not yet decisive — "
                 "monitor closely.",
        "hold": "Verdict HOLD: no ingested evidence materially drifts from the stated thesis.",
    }[verdict]

    pnl = (
        f"open P&L {c.pnl_pct * 100:+.1f}%" if c.pnl_pct is not None else "open P&L n/a"
    )
    nearest = (
        f"nearest catalyst {c.nearest_catalyst_days}d"
        if c.nearest_catalyst_days is not None
        else "no catalyst on file"
    )

    # Prefer the most-severe bear's invalidator; otherwise a generic watch line.
    if c.bears:
        top_bear = max(c.bears, key=lambda b: b.severity_of_concern)
        change_line = (
            f"Would change if: {top_bear.invalidator.strip()[:160]}"
            if top_bear.invalidator
            else "Would change on disclosure that contradicts the cited concerns."
        )
    else:
        change_line = "No catalog pattern currently pressures the thesis; revisit on new evidence."

    return "\n".join([
        f"Thesis rests on: {thesis_line}",
        f"Evidence: {len(c.scores)} scored item(s), {len(c.bears)} red-team pass(es); "
        f"peak composite {c.max_composite:.1f}, peak red-team severity {c.max_severity}/5.",
        f"Position: {c.pct_nav * 100:.1f}% NAV, {pnl}; {nearest}.",
        rationale,
        change_line,
    ])


# Run the decision engine across the portfolio (or one ticker). Skips holdings
# whose (thesis + evidence) are unchanged since the last compute unless force.
# Returns counts for logging/CLI.
def run_decisions(
    *,
    offline: bool = False,
    limit: int | None = None,
    only_ticker: str | None = None,
    force: bool = False,
    compute_source: str = "scheduler",
    evidence_article_event_ids: Sequence[str] | None = None,
) -> dict:
    _init_read_side_schemas()
    init_decision_schema()
    holdings, missing, _pulled_at = latest_joined()
    if only_ticker:
        want = only_ticker.upper()
        holdings = [h for h in holdings if h.ticker == want]
    if limit:
        holdings = holdings[:limit]

    if not holdings:
        log.warning("no_holdings_for_decisions", extra={"missing_sidecars": missing})
        return {"decided": 0, "skipped": 0, "errors": 0, "holdings": 0}

    # LLM provider when available; otherwise None → heuristic. --offline forces None.
    # W9: low-volume synthesis runs on the deeper "decision" tier.
    provider = get_provider(prefer_offline=offline, stage="decision")
    model_label = provider.model_label if provider else HEURISTIC_MODEL
    workers = llm_concurrency() if provider is not None else 1

    # Phase 1 (sequential reads): build each holding's candidate + hashes and
    # skip the ones whose thesis + evidence are unchanged since last compute.
    work: list[tuple[Holding, DecisionCandidate, str, str]] = []
    skipped = 0
    for h in holdings:
        candidate = build_candidate(h, evidence_article_event_ids=evidence_article_event_ids)
        th = thesis_hash(h.ticker, candidate.thesis)
        # Hash the uploaded-doc text + FMP metrics so a doc change or a fresh
        # financial snapshot re-computes this holding's decision.
        dh = event_id({"doc_text": candidate.thesis_doc_text}) if candidate.thesis_doc_text else ""
        fh = event_id({"fmp": candidate.fmp_metrics}) if candidate.fmp_metrics else ""
        tech_h = (
            event_id({"technical": candidate.technical.model_dump()})
            if candidate.technical
            else ""
        )
        ih = decision_inputs_hash(
            ticker=h.ticker,
            thesis_h=th,
            pct_nav=h.pct_nav,
            nearest_catalyst_days=h.nearest_catalyst_days,
            score_ids=[s.score_event_id for s in candidate.scores],
            pass_ids=[b.pass_event_id for b in candidate.bears],
            doc_hash=dh,
            fmp_hash=fh,
            technical_hash=tech_h,
        )
        if (
            not force
            and has_decision_for(h.ticker, ih, DECISION_VERSION)
            and has_rating_for(h.ticker, ih, RATING_VERSION)
        ):
            skipped += 1
            continue
        work.append((h, candidate, th, ih))

    # Per-holding compute (runs in the worker pool): ask Codex for the verdict.
    # FMP/aggregator corroboration is now handled by the Codex native web-search
    # instructions in the prompt instead of a Python Brave REST call.
    def _compute(
        item: tuple[Holding, DecisionCandidate, str, str],
    ) -> tuple[PositionDecision, PositionRating]:
        h, candidate, th, ih = item
        decision, llm_grade = decide_with_grade(
            candidate,
            th,
            ih,
            provider=provider,
            compute_source=compute_source,
        )
        rating = rate_candidate(
            candidate,
            thesis_h=th,
            inputs_h=ih,
            note=decision.note,
            drivers=decision.drivers,
            confidence=decision.confidence,
            model_used=decision.model_used,
            compute_source=compute_source,
            llm_grade=llm_grade,
        )
        return decision, rating

    # Phase 2 (bounded concurrency): compute verdicts. Sequential on the
    # heuristic path; ~SMA_LLM_CONCURRENCY calls when LLM-backed.
    results = map_concurrent(_compute, work, workers=workers)

    # Phase 3 (sequential writes): persist decisions, count failures.
    decided = errors = 0
    for (h, candidate, _th, _ih), (result, err) in zip(work, results, strict=False):
        if err is not None:
            log.error("decision_failed", extra={"ticker": h.ticker, "err": str(err)})
            errors += 1
            continue
        decision, rating = result
        save_decision(decision, decision_version=DECISION_VERSION)
        save_rating(rating)
        decided += 1
        log.info(
            "decision_done",
            extra={
                "ticker": h.ticker,
                "verdict": decision.verdict,
                "grade": rating.grade,
                "color": decision.color,
                "confidence": decision.confidence,
                "technical_state": rating.technical_state,
                "max_severity": candidate.max_severity,
                "max_composite": round(candidate.max_composite, 2),
            },
        )

    log.info(
        "decision_summary",
        extra={"decided": decided, "skipped": skipped, "errors": errors,
               "holdings": len(holdings), "model": model_label,
               "article_filter_count": (
                   len(evidence_article_event_ids)
                   if evidence_article_event_ids is not None
                   else None
               )},
    )
    return {"decided": decided, "skipped": skipped, "errors": errors, "holdings": len(holdings)}


def _init_read_side_schemas() -> None:
    init_db()
    init_news_schema()
    init_scores_schema()
    init_red_team_schema()

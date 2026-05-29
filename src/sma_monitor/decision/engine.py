"""Thesis-drift decision engine (Workstream 3).

For each holding it bundles the long thesis, scored articles, red-team bear
cases, catalysts, and open P&L (a DecisionCandidate), then produces one
HOLD/WATCH/SELL verdict with a short note. Offline-first: a deterministic
heuristic verdict (max red-team severity + composite band) runs whenever no
LLM provider is available; otherwise the Codex provider (W1) is asked for the
verdict under a strict output schema. A hard guard enforces the PLAN rule that
a red-team severity ≥ 4 can never resolve to HOLD.

Idempotency mirrors the scorer/red-team: inputs_hash captures the thesis plus
the exact evidence set, so run_decisions skips holdings whose thesis and
evidence are unchanged and re-computes the moment either moves.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import settings
from ..identity import event_id
from ..llm import get_provider
from ..llm.throughput import llm_concurrency, map_concurrent
from ..news.fmp_client import latest_fmp_metrics
from ..news.source_tiers import source_tier
from ..news.verification import verify_fmp
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from ..portfolio.uploads import combined_text
from ..red_team.store import recent_passes
from ..scorer.multipliers import T, T2
from ..scorer.store import recent_scores
from .prompt import build_system_prompt, build_user_message
from .schema import (
    BearEvidence,
    DecisionCandidate,
    PositionDecision,
    ScoreEvidence,
    VERDICT_COLOR,
)
from .store import has_decision_for, init_decision_schema, save_decision

log = logging.getLogger("sma_monitor.decision.engine")

# Version string the engine keys idempotency off. Bump when the prompt, the
# heuristic, or the candidate shape changes so every holding re-computes.
DECISION_VERSION = "v1.1-2026.05"

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
        "verdict": {"type": "string", "enum": ["hold", "watch", "sell"]},
        "note": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["verdict", "note", "drivers", "confidence"],
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
) -> str:
    return event_id({
        "kind": "decision_inputs",
        "ticker": ticker,
        "thesis_hash": thesis_h,
        "doc_hash": doc_hash,
        "fmp_hash": fmp_hash,
        "pct_nav": round(pct_nav, 4),
        "nearest_catalyst_days": nearest_catalyst_days,
        "score_ids": sorted(score_ids),
        "pass_ids": sorted(pass_ids),
        "decision_version": DECISION_VERSION,
    })


# Assemble a DecisionCandidate for one holding from the joined view plus the
# top scored articles and red-team passes on that ticker.
def build_candidate(holding: Holding) -> DecisionCandidate:
    scores = [_to_score_evidence(r) for r in recent_scores(ticker=holding.ticker, limit=EVIDENCE_LIMIT)]
    bears = [_to_bear_evidence(r) for r in recent_passes(ticker=holding.ticker, limit=EVIDENCE_LIMIT)]

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


# Decide one holding. Uses the LLM provider when supplied, else the heuristic;
# either way the sev≥4 guard and verdict→color mapping are applied last.
def decide(
    candidate: DecisionCandidate,
    thesis_h: str,
    inputs_h: str,
    *,
    provider=None,
) -> PositionDecision:
    if provider is None:
        verdict, note, drivers, confidence, model_used = _decide_heuristic(candidate)
    else:
        verdict, note, drivers, confidence, model_used = _decide_llm(candidate, provider)

    # PLAN guard: a red-team severity ≥ 4 can never resolve to HOLD.
    if candidate.max_severity >= 4 and verdict == "hold":
        verdict = "watch"
        note = (note.rstrip() + " ").strip() + (
            " (Auto-escalated to WATCH: an active red-team concern scores "
            f"severity {candidate.max_severity}/5.)"
        )

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
        decided_at=datetime.now(timezone.utc),
    )


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
    verdict = data.get("verdict")
    if verdict not in ("hold", "watch", "sell"):
        verdict = "watch"  # unparseable verdict is treated as needing attention
    note = (data.get("note") or "").strip()
    drivers = [str(d).strip() for d in (data.get("drivers") or []) if str(d).strip()]
    confidence = float(data.get("confidence", 0.5))
    return verdict, note, drivers, confidence, provider.model_label


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
        drivers.append(f"top score {top.composite:.1f} (#{top.primary_bucket_id}): {top.title[:60]}")
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
        change_line = f"Would change if: {top_bear.invalidator.strip()[:160]}" if top_bear.invalidator \
            else "Would change on disclosure that contradicts the cited concerns."
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
) -> dict:
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

    # Codex when available; otherwise None → heuristic. --offline forces None.
    # W9: low-volume synthesis runs on the deeper "decision" tier.
    provider = get_provider(prefer_offline=offline, stage="decision")
    model_label = provider.model_label if provider else HEURISTIC_MODEL
    workers = llm_concurrency() if provider is not None else 1

    # Phase 1 (sequential reads): build each holding's candidate + hashes and
    # skip the ones whose thesis + evidence are unchanged since last compute.
    work: list[tuple[Holding, DecisionCandidate, str, str]] = []
    skipped = 0
    for h in holdings:
        candidate = build_candidate(h)
        th = thesis_hash(h.ticker, candidate.thesis)
        # Hash the uploaded-doc text + FMP metrics so a doc change or a fresh
        # financial snapshot re-computes this holding's decision.
        dh = event_id({"doc_text": candidate.thesis_doc_text}) if candidate.thesis_doc_text else ""
        fh = event_id({"fmp": candidate.fmp_metrics}) if candidate.fmp_metrics else ""
        ih = decision_inputs_hash(
            ticker=h.ticker,
            thesis_h=th,
            pct_nav=h.pct_nav,
            nearest_catalyst_days=h.nearest_catalyst_days,
            score_ids=[s.score_event_id for s in candidate.scores],
            pass_ids=[b.pass_event_id for b in candidate.bears],
            doc_hash=dh,
            fmp_hash=fh,
        )
        if not force and has_decision_for(h.ticker, ih, DECISION_VERSION):
            skipped += 1
            continue
        work.append((h, candidate, th, ih))

    # Per-holding compute (runs in the worker pool): corroborate the FMP
    # financials against the web (Brave) so Codex can weigh them per the source
    # policy — display-only, deliberately NOT in inputs_hash — then ask for the
    # verdict. Live Brave call is skipped offline / keyless.
    def _compute(item: tuple[Holding, DecisionCandidate, str, str]) -> PositionDecision:
        h, candidate, th, ih = item
        if not offline and candidate.fmp_metrics and settings.brave_search_api_key:
            try:
                ver = verify_fmp(candidate.company_name or candidate.ticker,
                                 api_key=settings.brave_search_api_key)
                candidate.fmp_corroboration = {
                    "corroborated": ver.corroborated,
                    "sources": [{"title": s.title, "url": s.url, "tier": source_tier(s.url)}
                                for s in ver.sources[:3]],
                }
            except Exception as e:
                log.warning("fmp_verification_skipped", extra={"ticker": h.ticker, "err": str(e)})
        return decide(candidate, th, ih, provider=provider)

    # Phase 2 (bounded concurrency): compute verdicts. Sequential on the
    # heuristic path; ~SMA_LLM_CONCURRENCY codex processes when LLM-backed.
    results = map_concurrent(_compute, work, workers=workers)

    # Phase 3 (sequential writes): persist decisions, count failures.
    decided = errors = 0
    for (h, candidate, _th, _ih), (decision, err) in zip(work, results):
        if err is not None:
            log.error("decision_failed", extra={"ticker": h.ticker, "err": str(err)})
            errors += 1
            continue
        save_decision(decision, decision_version=DECISION_VERSION)
        decided += 1
        log.info(
            "decision_done",
            extra={
                "ticker": h.ticker,
                "verdict": decision.verdict,
                "color": decision.color,
                "confidence": decision.confidence,
                "max_severity": candidate.max_severity,
                "max_composite": round(candidate.max_composite, 2),
            },
        )

    log.info(
        "decision_summary",
        extra={"decided": decided, "skipped": skipped, "errors": errors,
               "holdings": len(holdings), "model": model_label},
    )
    return {"decided": decided, "skipped": skipped, "errors": errors, "holdings": len(holdings)}

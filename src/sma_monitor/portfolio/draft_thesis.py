"""Research-backed preliminary theses for positions without PM-authored theses.

The portfolio manager remains the source of truth. This module creates clearly
marked, versioned AI drafts for new positions and can explicitly upgrade older
AI drafts without ever overwriting a PM-authored active thesis.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ..analyst_targets.store import latest_target_state
from ..decision.engine import build_candidate, decision_inputs_hash, thesis_hash
from ..decision.rating import RATING_VERSION, rate_candidate
from ..decision.store import init_decision_schema, save_rating
from ..identity import event_id
from ..llm import get_provider
from ..llm.throughput import llm_concurrency, map_concurrent
from ..news.fmp_client import latest_fmp_metrics
from ..news.store import recent_articles
from .joined import join
from .schema import (
    Position,
    PreliminaryThesis,
    Sidecar,
    ThesisResearchSource,
)
from .sidecar import init_sidecar_schema, load_all_sidecars, write_sidecar
from .store import latest_positions

log = logging.getLogger("sma_monitor.portfolio.draft_thesis")

DRAFT_COMPUTE_SOURCE = "scheduler_new_position_draft"
PRELIMINARY_THESIS_VERSION = "sector_neutral_research_v2"
AI_DRAFT_PREFIX = "AI-generated preliminary thesis (PM review required):"

DRAFT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": ["string", "null"]},
        "sector": {"type": ["string", "null"]},
        "security_type": {
            "type": "string",
            "enum": ["operating_company", "etf", "other"],
        },
        "stage": {
            "type": "string",
            "enum": ["clinical_stage", "commercial_stage", "hybrid"],
        },
        "conviction_tier": {"type": "integer", "minimum": 1, "maximum": 5},
        "investment_case": {"type": "string"},
        "moat": {"type": "string"},
        "catalysts": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "differentiation": {"type": "string"},
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "monitoring_points": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "source_type": {
                        "type": "string",
                        "enum": [
                            "company",
                            "filing",
                            "regulatory",
                            "clinical",
                            "market",
                            "other",
                        ],
                    },
                },
                "required": ["title", "url", "source_type"],
            },
            "maxItems": 10,
        },
        "initial_grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "grade_rationale": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "company_name",
        "sector",
        "security_type",
        "stage",
        "conviction_tier",
        "investment_case",
        "moat",
        "catalysts",
        "differentiation",
        "risks",
        "monitoring_points",
        "sources",
        "initial_grade",
        "grade_rationale",
        "drivers",
        "confidence",
    ],
}


def bootstrap_ai_draft_sidecars(
    *,
    positions: Sequence[Position] | None = None,
    provider=None,
    compute_source: str = DRAFT_COMPUTE_SOURCE,
    limit: int | None = None,
    only_tickers: Sequence[str] | None = None,
    upgrade_existing_ai: bool = False,
    workers: int | None = None,
) -> dict:
    """Create or explicitly upgrade researched AI drafts for held positions.

    PM-authored active theses are immutable in this workflow. Legacy AI drafts
    are upgraded only when ``upgrade_existing_ai`` is true, which keeps routine
    broker refreshes from repeatedly spending model calls or rewriting drafts.
    """
    init_sidecar_schema()
    init_decision_schema()
    if positions is None:
        positions, _pulled_at = latest_positions()
    wanted = {
        ticker.strip().upper()
        for ticker in (only_tickers or [])
        if ticker and ticker.strip()
    }
    positions = [p for p in positions if not wanted or p.ticker in wanted]
    sidecars = load_all_sidecars()

    state: dict[str, Any] = {
        "created": 0,
        "updated_placeholders": 0,
        "upgraded_existing_ai": 0,
        "ratings_created": 0,
        "ratings_preserved": 0,
        "created_tickers": [],
        "updated_tickers": [],
        "upgraded_tickers": [],
        "skipped_existing_pm": [],
        "skipped_existing_ai_draft": [],
        "skipped_current_ai_draft": [],
        "skipped_no_provider": [],
        "failed": [],
        "compute_source": compute_source,
        "draft_version": PRELIMINARY_THESIS_VERSION,
    }

    eligible: list[tuple[Position, Sidecar | None, str]] = []
    for position in positions:
        sidecar = sidecars.get(position.ticker)
        if sidecar is None:
            eligible.append((position, None, "missing"))
            continue
        is_legacy_ai = (
            sidecar.thesis_source == "ai_generated"
            and sidecar.thesis_status == "draft"
        ) or _is_legacy_ai_text(sidecar.thesis)
        if is_legacy_ai:
            current_version = (
                sidecar.preliminary_thesis.version
                if sidecar.preliminary_thesis is not None
                else None
            )
            if current_version == PRELIMINARY_THESIS_VERSION:
                state["skipped_current_ai_draft"].append(position.ticker)
                continue
            if not upgrade_existing_ai:
                state["skipped_existing_ai_draft"].append(position.ticker)
                continue
            eligible.append((position, sidecar, "legacy_ai"))
            continue
        if _has_pm_thesis(sidecar):
            state["skipped_existing_pm"].append(position.ticker)
            continue
        eligible.append((position, sidecar, "placeholder"))

    if limit is not None:
        eligible = eligible[: max(0, int(limit))]
    if not eligible:
        return state

    provider = provider or get_provider(prefer_offline=False, stage="draft_thesis")
    if provider is None:
        state["skipped_no_provider"] = [
            position.ticker for position, _sidecar, _reason in eligible
        ]
        log.warning(
            "preliminary_thesis_skipped_no_provider",
            extra={"tickers": state["skipped_no_provider"]},
        )
        return state
    state["provider_model"] = getattr(provider, "model_label", "unknown")

    work = [
        (position, existing, reason, _research_context(position, existing))
        for position, existing, reason in eligible
    ]

    def _generate(item):
        position, _existing, _reason, research = item
        return _request_draft(provider, position, research)

    worker_count = max(1, min(4, workers or llm_concurrency()))
    state["workers"] = worker_count
    state["batches_total"] = (len(work) + worker_count - 1) // worker_count
    state["batches_completed"] = 0

    # Persist after each worker-sized wave. A long one-time backfill can then
    # resume after a timeout/restart without discarding already-finished names.
    for start in range(0, len(work), worker_count):
        batch = work[start : start + worker_count]
        results = map_concurrent(_generate, batch, workers=worker_count)
        for item, result in zip(batch, results, strict=True):
            _persist_draft_result(
                item,
                result,
                state=state,
                sidecars=sidecars,
                provider=provider,
                compute_source=compute_source,
            )
        state["batches_completed"] += 1
        log.info(
            "preliminary_thesis_batch_completed",
            extra={
                "batch": state["batches_completed"],
                "batches_total": state["batches_total"],
                "upgraded": state["upgraded_existing_ai"],
                "failed": len(state["failed"]),
            },
        )
    return state


def _persist_draft_result(
    item,
    result,
    *,
    state: dict[str, Any],
    sidecars: dict[str, Sidecar],
    provider,
    compute_source: str,
) -> None:
    position, existing, reason, research = item
    payload, error = result
    if error is not None:
        msg = str(error)[:240]
        state["failed"].append({"ticker": position.ticker, "error": msg})
        log.error(
            "preliminary_thesis_failed",
            extra={"ticker": position.ticker, "err": msg},
        )
        return
    try:
        sidecar = _sidecar_from_payload(
            position,
            dict(payload or {}),
            research=research,
            provider_label=getattr(provider, "model_label", "unknown"),
            compute_source=compute_source,
        )
        _preserve_discovered_metadata(sidecar, existing)
        if reason == "legacy_ai" and existing is not None:
            _preserve_draft_assessment(sidecar, existing)
        write_sidecar(sidecar)
        sidecars[position.ticker] = sidecar
        if reason == "missing":
            state["created"] += 1
            state["created_tickers"].append(position.ticker)
        elif reason == "legacy_ai":
            state["upgraded_existing_ai"] += 1
            state["upgraded_tickers"].append(position.ticker)
            state["ratings_preserved"] += 1
        else:
            state["updated_placeholders"] += 1
            state["updated_tickers"].append(position.ticker)

        if reason != "legacy_ai" and _save_initial_rating(
            position,
            sidecar,
            dict(payload or {}),
            provider,
            compute_source=compute_source,
        ):
            state["ratings_created"] += 1
    except Exception as exc:  # noqa: BLE001 - one bad draft must not block the book.
        msg = str(exc)[:240]
        state["failed"].append({"ticker": position.ticker, "error": msg})
        log.error(
            "preliminary_thesis_persist_failed",
            extra={"ticker": position.ticker, "err": msg},
        )


def _has_pm_thesis(sidecar: Sidecar) -> bool:
    return (
        sidecar.thesis_source == "pm"
        and sidecar.thesis_status == "active"
        and not _is_placeholder(sidecar.thesis)
    )


def _is_placeholder(thesis: str) -> bool:
    text = (thesis or "").strip().upper()
    return (
        not text
        or text.startswith("STUB")
        or text.startswith("PLACEHOLDER")
        or _is_legacy_ai_text(thesis)
    )


def _is_legacy_ai_text(thesis: str) -> bool:
    text = (thesis or "").strip().lower()
    return text.startswith("ai-generated draft thesis") or text.startswith(
        "ai-generated preliminary thesis"
    )


def _research_context(position: Position, existing: Sidecar | None) -> dict[str, Any]:
    fmp = latest_fmp_metrics(position.ticker) or {}
    target = latest_target_state(position.ticker)
    articles = []
    for row in recent_articles(ticker=position.ticker, limit=10):
        item = dict(row)
        articles.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "source": item.get("source"),
            }
        )
    security_type = "operating_company"
    if fmp.get("is_etf") or fmp.get("is_fund") or (target or {}).get("status") == "not_applicable":
        security_type = "etf"
    return {
        "company_name": (existing.company_name if existing else None) or fmp.get("company"),
        "sector": fmp.get("sector"),
        "security_type": security_type,
        "fmp_metrics": fmp,
        "analyst_target": target,
        "known_metadata": {
            "aliases": existing.aliases if existing else [],
            "brands": existing.brands if existing else [],
            "products": existing.products if existing else [],
            "indications": existing.indications if existing else [],
            "catalysts": [
                catalyst.model_dump(mode="json")
                for catalyst in (existing.catalysts if existing else [])
                if not catalyst.resolved
            ],
            "ir_url": existing.ir_url if existing else None,
            "press_releases_url": existing.press_releases_url if existing else None,
        },
        "recent_saved_articles": articles,
    }


def _request_draft(provider, position: Position, research: dict[str, Any]) -> dict:
    target = research.get("analyst_target") or {}
    is_etf = research.get("security_type") == "etf"
    target_context = (
        "Not applicable: this security is classified as an ETF/fund."
        if is_etf
        else json.dumps(
            {
                "source": target.get("source"),
                "status": target.get("status"),
                "mean_price_target": target.get("mean_price_target"),
                "analyst_count": target.get("analyst_count"),
                "currency": target.get("currency"),
                "target_fetched_at": target.get("target_fetched_at"),
                "upside_pct": target.get("upside_pct"),
                "price_as_of": target.get("price_as_of"),
            },
            default=str,
        )
    )
    fmp_json = json.dumps(research.get("fmp_metrics") or {}, default=str)
    metadata_json = json.dumps(research.get("known_metadata") or {}, default=str)
    articles_json = json.dumps(research.get("recent_saved_articles") or [], default=str)
    data = provider.complete_json(
        system=(
            "You are the AI-CRO research analyst for a multi-sector long-only SMA. "
            "The portfolio emphasizes biotech/pharma and technology but may own companies "
            "from any sector and ETFs. Never reject or weaken a thesis merely because a "
            "company is outside healthcare. Research the security with Codex native web "
            "search before answering. Prefer current primary sources: company investor "
            "relations materials, SEC filings, regulators, ClinicalTrials.gov, and product "
            "documentation. Use reputable market sources only as secondary context. "
            "Distinguish verified facts from inference, do not invent catalysts or dates, "
            "and return JSON only. This is an AI-generated preliminary thesis for PM review, "
            "not a PM-authored thesis or investment recommendation."
        ),
        user=(
            "POSITION\n"
            f"Ticker: {position.ticker}\n"
            f"Company: {research.get('company_name') or 'unknown'}\n"
            f"Cached sector: {research.get('sector') or 'unknown'}\n"
            f"Security type: {research.get('security_type')}\n"
            f"Quantity: {position.qty}\n"
            f"Market value: {position.market_value:.2f}\n"
            f"Portfolio weight: {position.pct_nav * 100:.2f}% NAV\n"
            f"Cost basis: {position.cost_basis if position.cost_basis is not None else 'unknown'}\n"
            f"Pulled at: {position.pulled_at.isoformat()}\n\n"
            "CACHED RESEARCH INPUTS\n"
            f"FMP profile/metrics: {fmp_json}\n"
            f"Known sidecar metadata: {metadata_json}\n"
            f"Recent saved articles: {articles_json}\n"
            f"Live {str(target.get('source') or 'FMP').upper()} consensus context: "
            f"{target_context}\n\n"
            "RESEARCH AND OUTPUT REQUIREMENTS\n"
            "- Identify the issuer/fund correctly before forming the thesis.\n"
            "- investment_case: concise preliminary reason the position may deserve "
            "ownership.\n"
            "- moat: durable competitive advantage or, for an ETF, its exposure and "
            "implementation edge.\n"
            "- catalysts: 2-5 concrete upcoming events with timing when verifiable; "
            "say when no near-term catalyst was verified.\n"
            "- differentiation: products, pipeline, technology, distribution, cost "
            "structure, or portfolio construction versus named alternatives.\n"
            "- risks and monitoring_points: specific falsifiers and evidence the PM "
            "should watch.\n"
            "- Sell-side consensus is valuation/sentiment context, not intrinsic "
            "value or fundamental proof. Do not copy its number into prose because "
            "the UI renders the live value separately.\n"
            "- ETFs have no company analyst PT: focus on methodology, exposure, fees, "
            "concentration, liquidity, and macro drivers.\n"
            "- sources: include 3-8 URLs actually used, prioritizing primary sources. "
            "Ground every catalyst and product claim.\n"
            "- stage: clinical_stage only for pre-commercial clinical companies; "
            "commercial_stage for operating businesses/ETFs; hybrid when both "
            "clinical and commercial assets are material.\n"
            "- initial_grade: A=clean hold, B=monitor, C=watch, D=sell/broken; keep "
            "confidence conservative until PM review.\n"
        ),
        schema=DRAFT_OUTPUT_SCHEMA,
        max_tokens=2400,
    )
    try:
        from ..orchestrator.cost import record_llm_call

        record_llm_call(
            kind="preliminary_thesis",
            model=getattr(provider, "model_label", "unknown"),
        )
    except Exception:
        pass
    return dict(data)


def _sidecar_from_payload(
    position: Position,
    payload: dict,
    *,
    research: dict[str, Any],
    provider_label: str,
    compute_source: str,
) -> Sidecar:
    now = datetime.now(UTC)
    security_type = _clean_security_type(
        "etf" if research.get("security_type") == "etf" else payload.get("security_type")
    )
    investment_case = _clean_section(
        payload.get("investment_case") or payload.get("thesis"),
        fallback=(
            f"{position.ticker} was newly detected in the portfolio. Company-specific "
            "facts remain insufficient for a reliable preliminary investment case."
        ),
    )
    moat = _clean_section(
        payload.get("moat"),
        fallback="No durable moat was verified in the available research.",
    )
    catalysts = _clean_list(
        payload.get("catalysts"),
        fallback=["No verified near-term catalyst was identified."],
        limit=5,
    )
    differentiation = _clean_section(
        payload.get("differentiation"),
        fallback="Competitive differentiation requires further PM verification.",
    )
    risks = _clean_list(
        payload.get("risks"),
        fallback=["The preliminary research may be incomplete or become stale."],
        limit=5,
    )
    monitoring_points = _clean_list(
        payload.get("monitoring_points"),
        fallback=["Verify the core thesis and upcoming milestones against primary sources."],
        limit=5,
    )
    sources = _clean_sources(payload.get("sources"))
    preliminary = PreliminaryThesis(
        version=PRELIMINARY_THESIS_VERSION,
        security_type=security_type,
        sector=_clean_optional_str(payload.get("sector") or research.get("sector")),
        investment_case=investment_case,
        moat=moat,
        catalysts=catalysts,
        differentiation=differentiation,
        risks=risks,
        monitoring_points=monitoring_points,
        research_sources=sources,
        researched_at=now,
    )
    drivers = _clean_drivers(payload.get("drivers"))
    grade = _clean_grade(payload.get("initial_grade"))
    note = str(
        payload.get("grade_rationale")
        or "Initial AI-generated rating; PM review required."
    ).strip()
    return Sidecar(
        ticker=position.ticker,
        conviction_tier=_clean_tier(payload.get("conviction_tier")),
        stage=_clean_stage(payload.get("stage"), security_type=security_type),
        thesis=_compose_thesis(preliminary),
        company_name=_clean_optional_str(
            payload.get("company_name") or research.get("company_name")
        ),
        thesis_source="ai_generated",
        thesis_status="draft",
        thesis_generated_by=provider_label,
        thesis_generated_at=now,
        thesis_compute_source=compute_source,
        preliminary_thesis=preliminary,
        draft_rating_grade=grade,
        draft_rating_note=note,
        draft_rating_confidence=_clean_confidence(payload.get("confidence")),
        draft_rating_drivers=drivers,
    )


def _preserve_discovered_metadata(sidecar: Sidecar, existing: Sidecar | None) -> None:
    if existing is None:
        return
    sidecar.ir_url = existing.ir_url
    sidecar.press_releases_url = existing.press_releases_url
    sidecar.press_release_rss_url = existing.press_release_rss_url
    sidecar.aliases = existing.aliases
    sidecar.brands = existing.brands
    sidecar.products = existing.products
    sidecar.indications = existing.indications
    sidecar.catalysts = existing.catalysts
    if existing.company_name and not sidecar.company_name:
        sidecar.company_name = existing.company_name


def _preserve_draft_assessment(sidecar: Sidecar, existing: Sidecar) -> None:
    """Keep the pre-existing grade metadata during a thesis-only upgrade."""
    sidecar.draft_rating_grade = existing.draft_rating_grade
    sidecar.draft_rating_note = existing.draft_rating_note
    sidecar.draft_rating_confidence = existing.draft_rating_confidence
    sidecar.draft_rating_drivers = existing.draft_rating_drivers


def _compose_thesis(preliminary: PreliminaryThesis) -> str:
    sections = [
        f"{AI_DRAFT_PREFIX} {preliminary.investment_case}",
        f"Moat\n{preliminary.moat}",
        "Catalysts\n" + "\n".join(f"- {item}" for item in preliminary.catalysts),
        f"Differentiation\n{preliminary.differentiation}",
        "Key risks\n" + "\n".join(f"- {item}" for item in preliminary.risks),
        "Monitor\n" + "\n".join(f"- {item}" for item in preliminary.monitoring_points),
    ]
    return "\n\n".join(sections)[:12000]


def _save_initial_rating(
    position: Position,
    sidecar: Sidecar,
    payload: dict,
    provider,
    *,
    compute_source: str,
) -> bool:
    holdings, missing = join([position], {position.ticker: sidecar})
    if missing or not holdings:
        return False
    candidate = build_candidate(holdings[0])
    th = thesis_hash(position.ticker, candidate.thesis)
    doc_hash = (
        event_id({"doc_text": candidate.thesis_doc_text})
        if candidate.thesis_doc_text
        else ""
    )
    fmp_hash = event_id({"fmp": candidate.fmp_metrics}) if candidate.fmp_metrics else ""
    technical_hash = (
        event_id({"technical": candidate.technical.model_dump()}) if candidate.technical else ""
    )
    ih = decision_inputs_hash(
        ticker=position.ticker,
        thesis_h=th,
        pct_nav=position.pct_nav,
        nearest_catalyst_days=holdings[0].nearest_catalyst_days,
        score_ids=[score.score_event_id for score in candidate.scores],
        pass_ids=[bear.pass_event_id for bear in candidate.bears],
        doc_hash=doc_hash,
        fmp_hash=fmp_hash,
        technical_hash=technical_hash,
    )
    rating = rate_candidate(
        candidate,
        thesis_h=th,
        inputs_h=ih,
        note=str(
            payload.get("grade_rationale")
            or "Initial AI-generated preliminary rating; PM review required."
        ),
        drivers=_clean_drivers(payload.get("drivers")),
        confidence=_clean_confidence(payload.get("confidence")),
        model_used=getattr(provider, "model_label", "unknown"),
        compute_source=compute_source,
        llm_grade=_clean_grade(payload.get("initial_grade")),
    )
    rating.rating_version = RATING_VERSION
    save_rating(rating)
    return True


def _clean_stage(value: Any, *, security_type: str) -> str:
    if security_type == "etf":
        return "commercial_stage"
    return value if value in {"clinical_stage", "commercial_stage", "hybrid"} else "hybrid"


def _clean_security_type(value: Any) -> str:
    return value if value in {"operating_company", "etf", "other"} else "operating_company"


def _clean_tier(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


def _clean_grade(value: Any) -> str:
    grade = str(value or "B").strip().upper()
    return grade if grade in {"A", "B", "C", "D"} else "B"


def _clean_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _clean_drivers(value: Any) -> list[str]:
    return _clean_list(
        value,
        fallback=["AI-generated preliminary thesis", "PM review required"],
        limit=6,
    )


def _clean_list(value: Any, *, fallback: list[str], limit: int) -> list[str]:
    if not isinstance(value, list):
        return fallback
    out = [" ".join(str(item).strip().split()) for item in value if str(item).strip()]
    return (out or fallback)[:limit]


def _clean_section(value: Any, *, fallback: str) -> str:
    text = " ".join(str(value or "").strip().split())
    return (text or fallback)[:4000]


def _clean_sources(value: Any) -> list[ThesisResearchSource]:
    if not isinstance(value, list):
        return []
    sources: list[ThesisResearchSource] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = " ".join(str(item.get("title") or "").strip().split())
        if not url.startswith(("https://", "http://")) or not title or url in seen:
            continue
        source_type = item.get("source_type")
        if source_type not in {"company", "filing", "regulatory", "clinical", "market", "other"}:
            source_type = "other"
        sources.append(
            ThesisResearchSource(
                title=title[:300],
                url=url[:2000],
                source_type=source_type,
            )
        )
        seen.add(url)
    return sources[:10]


def _clean_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text[:300] or None

"""AI draft thesis bootstrap for newly-detected broker positions.

When a fresh IBKR/Flex pull introduces a ticker with no PM thesis sidecar, the
portfolio join would normally exclude it from dashboard monitoring. This module
creates a clearly marked AI-generated draft sidecar plus an initial rating row so
new holdings appear in the dashboard immediately, while preserving PM-authored
theses as the source of truth once edited.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ..decision.engine import build_candidate, decision_inputs_hash, thesis_hash
from ..decision.rating import RATING_VERSION, rate_candidate
from ..decision.store import init_decision_schema, save_rating
from ..identity import event_id
from ..llm import get_provider
from .joined import join
from .schema import Position, Sidecar
from .sidecar import init_sidecar_schema, load_all_sidecars, write_sidecar
from .store import latest_positions

log = logging.getLogger("sma_monitor.portfolio.draft_thesis")

DRAFT_COMPUTE_SOURCE = "scheduler_new_position_draft"
DRAFT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": ["string", "null"]},
        "stage": {"type": "string", "enum": ["clinical_stage", "commercial_stage", "hybrid"]},
        "conviction_tier": {"type": "integer", "minimum": 1, "maximum": 5},
        "thesis": {
            "type": "string",
            "description": "PM-review draft buying/monitoring thesis, explicitly labeled AI-generated.",
        },
        "initial_grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "grade_rationale": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "company_name",
        "stage",
        "conviction_tier",
        "thesis",
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
) -> dict:
    """Create AI draft sidecars/ratings for current positions lacking PM thesis.

    Existing PM-authored sidecars are never overwritten. Existing AI drafts are
    left in place until the PM edits them via set_thesis(), which marks the
    sidecar pm/active. Placeholder/system stubs are eligible for replacement
    because they are not PM-authored theses.
    """
    init_sidecar_schema()
    init_decision_schema()
    if positions is None:
        positions, _pulled_at = latest_positions()
    positions = list(positions)
    sidecars = load_all_sidecars()

    state: dict[str, Any] = {
        "created": 0,
        "updated_placeholders": 0,
        "ratings_created": 0,
        "created_tickers": [],
        "updated_tickers": [],
        "skipped_existing_pm": [],
        "skipped_existing_ai_draft": [],
        "skipped_no_provider": [],
        "failed": [],
        "compute_source": compute_source,
    }

    eligible: list[tuple[Position, Sidecar | None, str]] = []
    for p in positions:
        sc = sidecars.get(p.ticker)
        if sc is None:
            eligible.append((p, None, "missing"))
            continue
        if _has_pm_thesis(sc):
            state["skipped_existing_pm"].append(p.ticker)
            continue
        if sc.thesis_source == "ai_generated" and sc.thesis_status == "draft":
            state["skipped_existing_ai_draft"].append(p.ticker)
            continue
        eligible.append((p, sc, "placeholder"))

    if limit is not None:
        eligible = eligible[: max(0, int(limit))]
    if not eligible:
        return state

    provider = provider or get_provider(prefer_offline=False, stage="decision")
    if provider is None:
        state["skipped_no_provider"] = [p.ticker for p, _sc, _reason in eligible]
        log.warning("new_position_draft_skipped_no_provider", extra={"tickers": state["skipped_no_provider"]})
        return state
    state["provider_model"] = getattr(provider, "model_label", "unknown")

    for p, existing, reason in eligible:
        try:
            payload = _request_draft(provider, p)
            sc = _sidecar_from_payload(
                p,
                payload,
                provider_label=getattr(provider, "model_label", "unknown"),
                compute_source=compute_source,
            )
            # Preserve any non-thesis metadata already discovered on a system stub
            # (e.g. IR URLs) while replacing the non-PM placeholder thesis.
            if existing is not None:
                sc.ir_url = existing.ir_url
                sc.press_releases_url = existing.press_releases_url
                sc.press_release_rss_url = existing.press_release_rss_url
                sc.aliases = existing.aliases
                sc.brands = existing.brands
                sc.products = existing.products
                sc.indications = existing.indications
                sc.catalysts = existing.catalysts
                if existing.company_name and not sc.company_name:
                    sc.company_name = existing.company_name
            write_sidecar(sc)
            sidecars[p.ticker] = sc
            if reason == "missing":
                state["created"] += 1
                state["created_tickers"].append(p.ticker)
            else:
                state["updated_placeholders"] += 1
                state["updated_tickers"].append(p.ticker)

            if _save_initial_rating(p, sc, payload, provider, compute_source=compute_source):
                state["ratings_created"] += 1
        except Exception as e:  # noqa: BLE001 - one bad draft must not block the book.
            msg = str(e)[:240]
            state["failed"].append({"ticker": p.ticker, "error": msg})
            log.error("new_position_draft_failed", extra={"ticker": p.ticker, "err": msg})
    return state


def _has_pm_thesis(sc: Sidecar) -> bool:
    return sc.thesis_source == "pm" and sc.thesis_status == "active" and not _is_placeholder(sc.thesis)


def _is_placeholder(thesis: str) -> bool:
    text = (thesis or "").strip().upper()
    return not text or text.startswith("STUB") or text.startswith("PLACEHOLDER")


def _request_draft(provider, position: Position) -> dict:
    data = provider.complete_json(
        system=(
            "You are the AI-CRO analyst for a biotech/healthcare-focused SMA dashboard. "
            "A broker position has appeared without a portfolio-manager thesis. "
            "Create a clearly labeled AI-generated draft thesis and initial A/B/C/D rating "
            "for PM review. Do not imply this is a PM-authored thesis. If company details are "
            "uncertain, say so and keep the grade conservative. Return JSON only."
        ),
        user=(
            "New broker position needing draft thesis bootstrap:\n"
            f"Ticker: {position.ticker}\n"
            f"Quantity: {position.qty}\n"
            f"Market value: {position.market_value:.2f}\n"
            f"Portfolio weight: {position.pct_nav * 100:.2f}% NAV\n"
            f"Cost basis: {position.cost_basis if position.cost_basis is not None else 'unknown'}\n"
            f"Pulled at: {position.pulled_at.isoformat()}\n\n"
            "Output requirements:\n"
            "- thesis: 1-3 paragraphs, starts with 'AI-generated draft thesis (PM review required):'\n"
            "- initial_grade: A/B/C/D using A=clean hold, B=monitor, C=watch, D=sell/broken\n"
            "- drivers: short concrete reasons or uncertainties\n"
            "- confidence should reflect uncertainty in an unreviewed draft."
        ),
        schema=DRAFT_OUTPUT_SCHEMA,
        max_tokens=900,
    )
    try:
        from ..orchestrator.cost import record_llm_call

        record_llm_call(kind="new_position_draft", model=getattr(provider, "model_label", "unknown"))
    except Exception:
        pass
    return dict(data)


def _sidecar_from_payload(
    position: Position,
    payload: dict,
    *,
    provider_label: str,
    compute_source: str,
) -> Sidecar:
    thesis = _clean_thesis(str(payload.get("thesis") or ""), position.ticker)
    drivers = _clean_drivers(payload.get("drivers"))
    grade = _clean_grade(payload.get("initial_grade"))
    note = str(payload.get("grade_rationale") or "Initial AI-generated rating; PM review required.").strip()
    return Sidecar(
        ticker=position.ticker,
        conviction_tier=_clean_tier(payload.get("conviction_tier")),
        stage=_clean_stage(payload.get("stage")),
        thesis=thesis,
        company_name=_clean_optional_str(payload.get("company_name")),
        thesis_source="ai_generated",
        thesis_status="draft",
        thesis_generated_by=provider_label,
        thesis_generated_at=datetime.now(UTC),
        thesis_compute_source=compute_source,
        draft_rating_grade=grade,
        draft_rating_note=note,
        draft_rating_confidence=_clean_confidence(payload.get("confidence")),
        draft_rating_drivers=drivers,
    )


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
    doc_hash = event_id({"doc_text": candidate.thesis_doc_text}) if candidate.thesis_doc_text else ""
    fmp_hash = event_id({"fmp": candidate.fmp_metrics}) if candidate.fmp_metrics else ""
    technical_hash = (
        event_id({"technical": candidate.technical.model_dump()}) if candidate.technical else ""
    )
    ih = decision_inputs_hash(
        ticker=position.ticker,
        thesis_h=th,
        pct_nav=position.pct_nav,
        nearest_catalyst_days=holdings[0].nearest_catalyst_days,
        score_ids=[s.score_event_id for s in candidate.scores],
        pass_ids=[b.pass_event_id for b in candidate.bears],
        doc_hash=doc_hash,
        fmp_hash=fmp_hash,
        technical_hash=technical_hash,
    )
    rating = rate_candidate(
        candidate,
        thesis_h=th,
        inputs_h=ih,
        note=str(payload.get("grade_rationale") or "Initial AI-generated draft rating; PM review required."),
        drivers=_clean_drivers(payload.get("drivers")),
        confidence=_clean_confidence(payload.get("confidence")),
        model_used=getattr(provider, "model_label", "unknown"),
        compute_source=compute_source,
        llm_grade=_clean_grade(payload.get("initial_grade")),
    )
    # Preserve the user's visible signal as an initial draft assessment. The
    # normal decision engine can recompute/replace it after PM review or fresh
    # evidence, but the new holding is dashboard-visible immediately.
    rating.rating_version = RATING_VERSION
    save_rating(rating)
    return True


def _clean_thesis(thesis: str, ticker: str) -> str:
    thesis = " ".join(thesis.strip().split())
    prefix = "AI-generated draft thesis (PM review required):"
    if not thesis:
        thesis = f"{prefix} {ticker} was newly detected in the broker feed. Company-specific thesis details are uncertain; PM review is required before relying on this assessment."
    elif not thesis.lower().startswith(prefix.lower()):
        thesis = f"{prefix} {thesis}"
    return thesis[:8000]


def _clean_stage(value: Any) -> str:
    return value if value in {"clinical_stage", "commercial_stage", "hybrid"} else "hybrid"


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
    if not isinstance(value, list):
        return ["AI-generated draft", "PM review required"]
    out = [str(v).strip() for v in value if str(v).strip()]
    return (out or ["AI-generated draft", "PM review required"])[:6]


def _clean_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

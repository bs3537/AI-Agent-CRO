"""Position routes (Workstream 5).

Wraps the existing pipeline functions — no new business logic:
  GET    /api/positions                      grid (P&L, %NAV, catalyst, decision)
  GET    /api/positions/{ticker}             detail (scores, red-team, files)
  PUT    /api/positions/{ticker}/thesis      edit thesis (sidecar.set_thesis)
  POST   /api/positions/{ticker}/files       upload a thesis doc (uploads.save_upload)
  DELETE /api/positions/{ticker}             delete a holding + ticker-owned monitor data
  DELETE /api/positions/{ticker}/files/{id}  remove an uploaded doc
  POST   /api/positions/{ticker}/recompute   run the decision engine (bg, or ?wait)
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from ...analyst_targets.store import latest_target_state, target_state_from_row
from ...config import settings
from ...db import connection
from ...decision.schema import GRADE_VERDICT, VERDICT_COLOR
from ...decision.store import latest_decision, latest_rating
from ...decision.technicals import technical_state
from ...news.catalyst_outlook import latest_catalyst_outlook
from ...news.fmp_client import latest_fmp_metrics, latest_price_series
from ...orchestrator.manual_recompute import (
    recompute_all_with_refresh,
    recompute_one_with_refresh,
)
from ...orchestrator.store import enqueue_runner_request, get_runner_request
from ...portfolio.delete import delete_holding_data
from ...portfolio.ir_urls import populate_ir_urls_for_tickers
from ...portfolio.joined import latest_joined
from ...portfolio.schema import Holding
from ...portfolio.sidecar import ensure_sidecar, set_thesis
from ...portfolio.store import latest_positions, save_manual_position
from ...portfolio.uploads import (
    UploadError,
    delete_file,
    list_files,
    save_upload,
)
from ...red_team.store import recent_passes
from ...scorer.store import recent_scores
from ..schemas import (
    CatalystOut,
    CatalystOutlookItemOut,
    CatalystTextUpdate,
    DecisionOut,
    DeleteHoldingResponse,
    FileOut,
    ManualPositionCreate,
    ManualPositionResponse,
    PositionDetail,
    PositionsResponse,
    PositionSummary,
    RatingOut,
    RecomputeAllResponse,
    RecomputeResponse,
    RecomputeStatusResponse,
    RedTeamOut,
    ScoreOut,
    SparklineOut,
    ThesisUpdate,
)

router = APIRouter(prefix="/api/positions", tags=["positions"])
UPLOAD_FILE = File(...)
RECOMPUTE_COMMANDS = {
    "manual_recompute_one",
    "manual_recompute_all",
    "preliminary_thesis_one",
    "preliminary_thesis_backfill",
    "thesis_recompute",
}
SECRETISH_ERROR_RE = re.compile(
    r"(?i)(api[_-]?key|auth[_-]?token|access[_-]?token|token|password|secret)=([^\s&]+)"
)


# Map a decision (+ its strongest red-team bear severity) to an A/B/C/D letter
# grade. The verdict sets the band; within a hold, severity then confidence
# split it. A = strong hold, B/C = hold (B solid, C weak/on-watch), D = sell.
def _grade(verdict: str, confidence: float, severity: int) -> str:
    if verdict == "sell":
        return "D"
    if verdict == "watch":
        return "C"
    # verdict == "hold"
    if severity >= 4:           # held, but a serious bear case is on the books
        return "C"
    if severity == 3 or confidence < 0.60:
        return "B"
    return "A"


# Highest red-team severity_of_concern recorded for a ticker (0 when none) —
# the bear-pressure signal that splits a hold into A/B/C.
def _max_severity(ticker: str) -> int:
    return max(
        (p["severity_of_concern"] or 0 for p in recent_passes(ticker=ticker, limit=50)),
        default=0,
    )


# Parse a position_decisions row into the wire shape (drivers stored as JSON),
# attaching the letter grade derived from the verdict + bear severity.
def _decision_out(row, severity: int = 0, rating: RatingOut | None = None) -> DecisionOut | None:
    if rating is not None:
        verdict = GRADE_VERDICT[rating.grade]
        return DecisionOut(
            verdict=verdict,
            color=VERDICT_COLOR[verdict],
            grade=rating.grade,
            note=rating.note,
            drivers=rating.drivers,
            confidence=rating.confidence,
            model_used=rating.model_used,
            compute_source=rating.compute_source,
            decided_at=rating.decided_at,
        )
    if row is None:
        return None
    try:
        drivers = json.loads(row["drivers"] or "[]")
    except (json.JSONDecodeError, TypeError):
        drivers = []
    return DecisionOut(
        verdict=row["verdict"], color=row["color"],
        grade=_grade(row["verdict"], row["confidence"], severity),
        note=row["note"], drivers=drivers, confidence=row["confidence"],
        model_used=row["model_used"],
        compute_source=row["compute_source"] or "unknown",
        decided_at=row["decided_at"],
    )


def _rating_out(row) -> RatingOut | None:
    if row is None:
        return None
    try:
        drivers = json.loads(row["drivers"] or "[]")
    except (json.JSONDecodeError, TypeError):
        drivers = []
    try:
        risk_components = json.loads(row["risk_components"] or "{}")
    except (json.JSONDecodeError, TypeError):
        risk_components = {}
    return RatingOut(
        action=row["action"],
        grade=row["grade"],
        attention_state=row["attention_state"],
        risk_score=row["risk_score"],
        risk_components=risk_components,
        latest_close=row["latest_close"],
        ema20=row["ema20"],
        price_vs_ema20_pct=row["price_vs_ema20_pct"],
        technical_state=row["technical_state"],
        deterministic_grade=row["deterministic_grade"],
        llm_grade=row["llm_grade"],
        final_grade=row["final_grade"],
        note=row["note"],
        drivers=drivers,
        confidence=row["confidence"],
        model_used=row["model_used"],
        compute_source=row["compute_source"] or "unknown",
        rating_version=row["rating_version"],
        decided_at=row["decided_at"],
    )


def _spark_out(ticker: str) -> SparklineOut | None:
    closes = latest_price_series(ticker)
    return _spark_from_closes(closes)


def _spark_from_closes(closes: list[float] | None) -> SparklineOut | None:
    if not closes:
        return None
    tech = technical_state(closes)
    return SparklineOut(
        closes=tech.closes,
        ema20=tech.ema20,
        latest_close=tech.latest_close,
        latest_ema20=tech.latest_ema20,
        price_vs_ema20_pct=tech.price_vs_ema20_pct,
        technical_state=tech.technical_state,
    )


def _dashboard_cache(tickers: list[str]) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {
        "decisions": {},
        "ratings": {},
        "file_counts": {},
        "severity": {},
        "prices": {},
        "targets": {},
        "financials": {},
        "catalyst_outlooks": {},
    }
    if not tickers:
        return cache

    placeholders = ",".join("?" for _ in tickers)
    with connection() as conn:
        for row in conn.execute(
            f"SELECT * FROM position_decisions WHERE ticker IN ({placeholders}) "
            "ORDER BY ticker, decided_at DESC",
            tickers,
        ).fetchall():
            cache["decisions"].setdefault(row["ticker"], row)

        for row in conn.execute(
            f"SELECT * FROM position_ratings WHERE ticker IN ({placeholders}) "
            "ORDER BY ticker, decided_at DESC",
            tickers,
        ).fetchall():
            cache["ratings"].setdefault(row["ticker"], row)

        for row in conn.execute(
            f"SELECT ticker, COUNT(*) AS n FROM position_files "
            f"WHERE ticker IN ({placeholders}) GROUP BY ticker",
            tickers,
        ).fetchall():
            cache["file_counts"][row["ticker"]] = int(row["n"] or 0)

        for row in conn.execute(
            f"SELECT ticker, MAX(COALESCE(severity_of_concern, 0)) AS severity "
            f"FROM red_team_passes WHERE ticker IN ({placeholders}) GROUP BY ticker",
            tickers,
        ).fetchall():
            cache["severity"][row["ticker"]] = int(row["severity"] or 0)

        for row in conn.execute(
            f"SELECT ticker, closes FROM price_series WHERE ticker IN ({placeholders}) "
            "ORDER BY ticker, fetched_at DESC",
            tickers,
        ).fetchall():
            ticker = row["ticker"]
            if ticker in cache["prices"]:
                continue
            try:
                cache["prices"][ticker] = json.loads(row["closes"] or "[]")
            except (TypeError, json.JSONDecodeError):
                cache["prices"][ticker] = None

        for row in conn.execute(
            f"SELECT * FROM analyst_price_targets WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall():
            cache["targets"][row["ticker"]] = row

        for row in conn.execute(
            f"SELECT ticker, metrics FROM fmp_snapshots WHERE ticker IN ({placeholders}) "
            "ORDER BY ticker, fetched_at DESC",
            tickers,
        ).fetchall():
            ticker = row["ticker"]
            if ticker in cache["financials"]:
                continue
            try:
                cache["financials"][ticker] = json.loads(row["metrics"] or "{}")
            except (TypeError, json.JSONDecodeError):
                cache["financials"][ticker] = {}

        for row in conn.execute(
            f"SELECT ticker, items_json FROM catalyst_outlooks "
            f"WHERE ticker IN ({placeholders}) ORDER BY ticker, searched_at DESC",
            tickers,
        ).fetchall():
            ticker = row["ticker"]
            if ticker in cache["catalyst_outlooks"]:
                continue
            cache["catalyst_outlooks"][ticker] = _catalyst_outlook_items(
                row["items_json"]
            )
    return cache


def _catalyst_outlook_items(value: str | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        try:
            items.append(CatalystOutlookItemOut.model_validate(item).model_dump())
        except (TypeError, ValueError):
            continue
    return items


# Build the grid-row summary for one holding: economics + nearest catalyst +
# latest decision + uploaded-file count.
def _summary(h: Holding, cache: dict[str, dict[str, Any]] | None = None) -> PositionSummary:
    open_pnl = pnl_pct = None
    if h.cost_basis is not None:
        open_pnl = h.market_value - h.cost_basis
        if h.cost_basis:
            pnl_pct = open_pnl / h.cost_basis
    if cache is None:
        dec_row = latest_decision(h.ticker)
        rating = _rating_out(latest_rating(h.ticker))
        n_files = len(list_files(h.ticker))
        spark = _spark_out(h.ticker)
        analyst_target = latest_target_state(h.ticker)
        catalyst_outlook = _catalyst_outlook_items(
            latest_catalyst_outlook(h.ticker)
        )
        is_etf = bool(
            (latest_fmp_metrics(h.ticker) or {}).get("is_etf")
            or (analyst_target or {}).get("status") == "not_applicable"
        )
        severity = _max_severity(h.ticker) if dec_row else 0
    else:
        dec_row = cache["decisions"].get(h.ticker)
        rating = _rating_out(cache["ratings"].get(h.ticker))
        n_files = int(cache["file_counts"].get(h.ticker, 0))
        spark = _spark_from_closes(cache["prices"].get(h.ticker))
        analyst_target = target_state_from_row(cache["targets"].get(h.ticker))
        catalyst_outlook = cache["catalyst_outlooks"].get(h.ticker, [])
        is_etf = bool(
            cache["financials"].get(h.ticker, {}).get("is_etf")
            or (analyst_target or {}).get("status") == "not_applicable"
        )
        severity = int(cache["severity"].get(h.ticker, 0)) if dec_row else 0
    return PositionSummary(
        ticker=h.ticker, company_name=h.company_name, stage=h.stage,
        conviction_tier=int(h.conviction_tier), qty=h.qty,
        market_value=h.market_value, cost_basis=h.cost_basis, pct_nav=h.pct_nav,
        open_pnl=open_pnl, pnl_pct=pnl_pct,
        nearest_catalyst_days=h.nearest_catalyst_days,
        has_overdue_catalyst=h.has_overdue_catalyst,
        thesis=h.thesis,
        thesis_source=h.thesis_source,
        thesis_status=h.thesis_status,
        is_ai_generated_thesis=h.thesis_source == "ai_generated",
        thesis_generated_by=h.thesis_generated_by,
        thesis_generated_at=h.thesis_generated_at.isoformat() if h.thesis_generated_at else None,
        thesis_compute_source=h.thesis_compute_source,
        preliminary_thesis=(
            h.preliminary_thesis.model_dump(mode="json")
            if h.preliminary_thesis is not None
            else None
        ),
        draft_rating_grade=h.draft_rating_grade,
        draft_rating_note=h.draft_rating_note,
        draft_rating_confidence=h.draft_rating_confidence,
        draft_rating_drivers=h.draft_rating_drivers,
        is_etf=is_etf,
        n_files=n_files,
        spark=spark,  # W6/V2: 1yr close sparkline + EMA20 overlay
        analyst_target=analyst_target,
        catalyst_outlook=catalyst_outlook,
        rating=rating,
        decision=_decision_out(dec_row, severity, rating),
    )


def _safe_runner_error(value: str | None) -> str | None:
    if not value:
        return None
    safe = SECRETISH_ERROR_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", str(value))
    safe = re.sub(r"([?&])([^\s=&#/?]+)=([^\s&#]+)", r"\1\2=[REDACTED]", safe)
    return safe[:1000]


# Resolve one ticker to its current Holding, or raise 404.
def _holding_or_404(ticker: str) -> Holding:
    want = ticker.strip().upper()
    holdings, _missing, _pulled = latest_joined()
    for h in holdings:
        if h.ticker == want:
            return h
    raise HTTPException(status_code=404, detail=f"no held position with a sidecar for {want}")


# Tickers present in the latest position pull (with or without a sidecar) —
# the set thesis edits are allowed for.
def _position_tickers() -> set[str]:
    positions, _ = latest_positions()
    return {p.ticker for p in positions}


# GET /api/positions — the dashboard grid.
@router.get("", response_model=PositionsResponse)
def list_positions() -> PositionsResponse:
    holdings, missing, pulled_at = latest_joined()
    cache = _dashboard_cache([h.ticker for h in holdings])
    return PositionsResponse(
        pulled_at=pulled_at.isoformat() if pulled_at else None,
        positions=[_summary(h, cache) for h in holdings],
        missing_sidecars=missing,
    )


# POST /api/positions — manually add a position by ticker + portfolio weight.
# A supplied thesis is PM-authored and follows the normal recompute path. When
# blank, create a visible stub and queue researched preliminary-thesis generation.
@router.post("", response_model=ManualPositionResponse, status_code=201)
def add_manual_position(
    body: ManualPositionCreate,
    offline: bool = False,
) -> ManualPositionResponse:
    from ...portfolio.store import latest_positions as _latest_positions_store

    want = body.ticker.strip().upper()

    # Derive qty and total cost basis from cost_basis_per_share + current price.
    cost_basis_total: float | None = None
    qty_est: float | None = None
    if body.cost_basis_per_share is not None and body.portfolio_weight_pct > 0:
        closes = latest_price_series(want)
        current_price = closes[-1] if closes else None
        if current_price and current_price > 0:
            all_pos, _ = _latest_positions_store()
            nav = all_pos[0].nav if all_pos else 1_000_000.0
            market_value = body.portfolio_weight_pct / 100.0 * nav
            qty_est = market_value / current_price
            cost_basis_total = body.cost_basis_per_share * qty_est

    save_manual_position(
        want,
        pct_nav=body.portfolio_weight_pct / 100.0,
        cost_basis=cost_basis_total,
        qty=qty_est,
    )
    supplied_thesis = body.thesis.strip()
    if supplied_thesis:
        set_thesis(want, supplied_thesis)
    else:
        ensure_sidecar(want)
    ir_state = _populate_ir_urls_best_effort(want, offline=offline)
    if settings.is_dashboard_role() and supplied_thesis:
        state = {
            "queued": enqueue_runner_request(
                command="manual_recompute_one",
                ticker=want,
                payload={
                    "offline": offline,
                    "compute_source": "hermes_manual_single",
                },
            )
        }
    elif settings.is_dashboard_role():
        state = {
            "queued": enqueue_runner_request(
                command="preliminary_thesis_one",
                ticker=want,
                payload={
                    "ticker": want,
                    "upgrade_existing_ai": True,
                    "refresh_inputs": not offline,
                    "compute_source": "hermes_manual_preliminary_thesis",
                },
            )
        }
    elif supplied_thesis:
        state = recompute_one_with_refresh(
            want,
            offline=offline,
            compute_source="manual_single",
        )
    else:
        from ...orchestrator.preliminary_thesis import run_preliminary_thesis_workflow

        state = run_preliminary_thesis_workflow(
            tickers=[want],
            upgrade_existing_ai=True,
            refresh_inputs=not offline,
            compute_source="manual_preliminary_thesis",
        )
    if ir_state is not None:
        state["ir_urls"] = ir_state
    return ManualPositionResponse(
        position=_summary(_holding_or_404(want)),
        refresh=state,
    )


def _populate_ir_urls_best_effort(ticker: str, *, offline: bool = False) -> dict | None:
    if offline:
        return {"status": "skipped", "reason": "offline"}
    try:
        return populate_ir_urls_for_tickers([ticker], create_missing=False)
    except Exception as e:  # noqa: BLE001 - position creation must not fail on IR discovery.
        return {"status": "failed", "reason": str(e)[:200]}


# GET /api/positions/{ticker} — detail with the full evidence trail.
@router.get("/{ticker}", response_model=PositionDetail)
def get_position(ticker: str) -> PositionDetail:
    h = _holding_or_404(ticker)
    summary = _summary(h)

    scores = [
        ScoreOut(
            score_event_id=r["event_id"], title=r["title"] or "", url=r["url"],
            primary_bucket_id=r["primary_bucket_id"], composite=r["composite"],
            threshold_band=r["threshold_band"],
            axes=(r["financial_impact"], r["narrative_shift"], r["time_criticality"]),
            rationale=r["rationale"] or "", confidence=r["confidence"],
            scored_at=r["scored_at"],
        )
        for r in recent_scores(ticker=h.ticker, limit=50)
    ]
    red_team = [
        RedTeamOut(
            pass_event_id=r["event_id"], title=r["title"] or "", url=r["url"],
            bearish_thesis=r["bearish_thesis"] or "",
            severity_of_concern=r["severity_of_concern"],
            matched_patterns=_pattern_ids(r["matched_warning_signs"]),
            invalidator=r["invalidator"] or "", ran_at=r["ran_at"],
        )
        for r in recent_passes(ticker=h.ticker, limit=50)
    ]
    files = [
        FileOut(
            event_id=r["event_id"], filename=r["filename"],
            content_type=r["content_type"], n_chars=r["n_chars"],
            byte_size=r["byte_size"], uploaded_at=r["uploaded_at"],
        )
        for r in list_files(ticker=h.ticker)
    ]
    catalysts = [
        CatalystOut(
            date=c.date.isoformat(), type=c.type, description=c.description,
            confidence=c.confidence, resolved=c.resolved,
        )
        for c in h.catalysts
    ]
    return PositionDetail(
        **summary.model_dump(),
        catalysts=catalysts, scores=scores, red_team=red_team, files=files,
        financials=latest_fmp_metrics(h.ticker),  # W2: FMP snapshot (None until fetched)
    )


# Extract warning-sign ids from a stored matched_warning_signs JSON blob.
def _pattern_ids(blob) -> list[str]:
    try:
        items = json.loads(blob or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [m.get("id") for m in items if m.get("id")]


# PUT /api/positions/{ticker}/catalyst_outlook — save PM-edited catalysts as
# plain text. Each non-empty line becomes one catalyst entry of type "other".
# This overwrites the latest catalyst_outlook record for the ticker.
@router.put("/{ticker}/catalyst_outlook", status_code=204)
def update_catalyst_outlook(ticker: str, body: CatalystTextUpdate) -> None:
    from ...news.catalyst_outlook import (
        CatalystOutlookItem as _CItem,
        save_catalyst_outlook as _save,
    )
    want = ticker.strip().upper()
    if want not in _position_tickers():
        raise HTTPException(status_code=404, detail=f"no held position for {want}")
    text = (body.text or "").strip()
    items: list[_CItem] = []
    if text:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            label = line[:80]
            date_label = "Manual entry"
            # Try to parse a leading date/label prefix like "Q4 2025: ..." or "2025-12-01: ..."
            if ": " in line:
                prefix, rest = line.split(": ", 1)
                date_label = prefix.strip()[:40]
                label = rest.strip()[:80]
            items.append(_CItem(
                date=None,
                date_label=date_label,
                type="other",
                label=label,
                confirmed=True,
                source_title="PM manual entry",
                source_url="manual",
            ))
    _save(want, items, model_used="pm_manual")


# PUT /api/positions/{ticker}/thesis — edit the long thesis. Creates a minimal
# sidecar if the position has none yet, then returns the refreshed summary.
@router.put("/{ticker}/thesis", response_model=PositionSummary)
def update_thesis(ticker: str, body: ThesisUpdate) -> PositionSummary:
    want = ticker.strip().upper()
    if want not in _position_tickers():
        raise HTTPException(status_code=404, detail=f"no held position for {want}")
    set_thesis(want, body.thesis)
    return _summary(_holding_or_404(want))


# POST /api/positions/{ticker}/files — upload a thesis document (multipart).
@router.post("/{ticker}/files", response_model=FileOut, status_code=201)
async def upload_file(ticker: str, file: UploadFile = UPLOAD_FILE) -> FileOut:
    want = ticker.strip().upper()
    if want not in _position_tickers():
        raise HTTPException(status_code=404, detail=f"no held position for {want}")
    content = await file.read()
    try:
        rec = save_upload(want, file.filename or "upload", content)
    except UploadError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    return FileOut(**{
        k: rec[k]
        for k in ("event_id", "filename", "content_type", "n_chars", "byte_size", "uploaded_at")
    })


# DELETE /api/positions/{ticker}/files/{event_id} — remove an uploaded doc.
@router.delete("/{ticker}/files/{event_id}", status_code=204)
def remove_file(ticker: str, event_id: str) -> None:
    if not delete_file(event_id):
        raise HTTPException(status_code=404, detail="file not found")


# DELETE /api/positions/{ticker} — operator override for closed/stale holdings.
@router.delete("/{ticker}", response_model=DeleteHoldingResponse)
def delete_holding(ticker: str) -> DeleteHoldingResponse:
    want = ticker.strip().upper()
    if want not in _position_tickers():
        raise HTTPException(status_code=404, detail=f"no held position for {want}")
    result = delete_holding_data(want)
    return DeleteHoldingResponse(ticker=want, deleted=result["deleted"])


# POST /api/positions/{ticker}/recompute — refresh ticker evidence, score and
# red-team new rows, then run the decision engine for one ticker. Background by
# default; ?wait=true runs inline and returns the saved result.
@router.post("/{ticker}/recompute", response_model=RecomputeResponse)
def recompute(
    ticker: str,
    background: BackgroundTasks,
    wait: bool = False,
    offline: bool = False,
    compute_source: str = "manual_single",
) -> RecomputeResponse:
    want = ticker.strip().upper()
    _holding_or_404(want)  # 404 if not a current holding
    if settings.is_dashboard_role():
        req = enqueue_runner_request(
            command="manual_recompute_one",
            ticker=want,
            payload={
                "offline": offline,
                "compute_source": compute_source or "hermes_manual_single",
            },
        )
        return RecomputeResponse(
            ticker=want,
            scheduled=True,
            request_id=req["request_id"],
            queue_status=req["status"],
            refresh={"queued": req},
        )
    if wait:
        state = recompute_one_with_refresh(
            want,
            offline=offline,
            compute_source=compute_source,
        )
        row = latest_decision(want)
        rating = _rating_out(latest_rating(want))
        return RecomputeResponse(
            ticker=want,
            scheduled=False,
            rating=rating,
            decision=_decision_out(row, _max_severity(want) if row else 0, rating),
            refresh=state,
        )
    background.add_task(
        recompute_one_with_refresh,
        want,
        offline=offline,
        compute_source=compute_source,
    )
    return RecomputeResponse(ticker=want, scheduled=True, decision=None)


# GET /api/positions/recompute/{request_id} — status for dashboard-queued
# recompute requests completed by the trusted VPS runner.
@router.get("/recompute/{request_id}", response_model=RecomputeStatusResponse)
def recompute_status(request_id: str) -> RecomputeStatusResponse:
    row = get_runner_request(request_id)
    if row is None or row.get("command") not in RECOMPUTE_COMMANDS:
        raise HTTPException(status_code=404, detail="recompute request not found")

    refresh = None
    if row.get("summary_json"):
        try:
            parsed = json.loads(row.get("summary_json") or "{}")
        except (json.JSONDecodeError, TypeError) as e:
            raise HTTPException(
                status_code=502,
                detail=f"recompute result unreadable: {str(e)[:200]}",
            ) from e
        refresh = parsed if isinstance(parsed, dict) else {"result": parsed}

    return RecomputeStatusResponse(
        request_id=row["request_id"],
        command=row["command"],
        ticker=row.get("ticker"),
        status=row["status"],
        refresh=refresh,
        error=_safe_runner_error(row.get("error")),
    )


# POST /api/positions/recompute — refresh evidence and recompute EVERY holding
# in one call. Background by default; ?wait=true runs inline and returns the
# {decided, skipped, errors, holdings} summary. This can be slow because it may
# run live search, scoring, red team, and decision LLM calls.
@router.post("/recompute", response_model=RecomputeAllResponse)
def recompute_all(
    background: BackgroundTasks,
    wait: bool = False,
    force: bool = True,
    offline: bool = False,
) -> RecomputeAllResponse:
    if settings.is_dashboard_role():
        req = enqueue_runner_request(
            command="manual_recompute_all",
            payload={"offline": offline, "force": force},
        )
        return RecomputeAllResponse(
            scheduled=True,
            request_id=req["request_id"],
            queue_status=req["status"],
            refresh={"queued": req},
        )
    if wait:
        state = recompute_all_with_refresh(offline=offline, force=force)
        s = state.get("decisions", {})
        return RecomputeAllResponse(
            scheduled=False,
            decided=s.get("decided", 0), skipped=s.get("skipped", 0),
            errors=s.get("errors", 0), holdings=s.get("holdings", 0),
            refresh=state,
        )
    background.add_task(recompute_all_with_refresh, offline=offline, force=force)
    return RecomputeAllResponse(scheduled=True)

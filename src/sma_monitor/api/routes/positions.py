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

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from ...decision.schema import GRADE_VERDICT, VERDICT_COLOR
from ...decision.store import latest_decision, latest_rating
from ...decision.technicals import technical_state
from ...news.fmp_client import latest_fmp_metrics, latest_price_series
from ...orchestrator.manual_recompute import (
    recompute_all_with_refresh,
    recompute_one_with_refresh,
)
from ...portfolio.delete import delete_holding_data
from ...portfolio.joined import latest_joined
from ...portfolio.schema import Holding
from ...portfolio.sidecar import set_thesis
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
    RedTeamOut,
    ScoreOut,
    SparklineOut,
    ThesisUpdate,
)

router = APIRouter(prefix="/api/positions", tags=["positions"])
UPLOAD_FILE = File(...)


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


# Build the grid-row summary for one holding: economics + nearest catalyst +
# latest decision + uploaded-file count.
def _summary(h: Holding) -> PositionSummary:
    open_pnl = pnl_pct = None
    if h.cost_basis is not None:
        open_pnl = h.market_value - h.cost_basis
        if h.cost_basis:
            pnl_pct = open_pnl / h.cost_basis
    dec_row = latest_decision(h.ticker)
    rating = _rating_out(latest_rating(h.ticker))
    return PositionSummary(
        ticker=h.ticker, company_name=h.company_name, stage=h.stage,
        conviction_tier=int(h.conviction_tier), qty=h.qty,
        market_value=h.market_value, cost_basis=h.cost_basis, pct_nav=h.pct_nav,
        open_pnl=open_pnl, pnl_pct=pnl_pct,
        nearest_catalyst_days=h.nearest_catalyst_days,
        has_overdue_catalyst=h.has_overdue_catalyst,
        thesis=h.thesis, n_files=len(list_files(h.ticker)),
        spark=_spark_out(h.ticker),  # W6/V2: 1yr close sparkline + EMA20 overlay
        rating=rating,
        decision=_decision_out(dec_row, _max_severity(h.ticker) if dec_row else 0, rating),
    )


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
    return PositionsResponse(
        pulled_at=pulled_at.isoformat() if pulled_at else None,
        positions=[_summary(h) for h in holdings],
        missing_sidecars=missing,
    )


# POST /api/positions — manually add a position by ticker + portfolio weight,
# then immediately run the same fresh-evidence + LLM recompute path as the tile.
@router.post("", response_model=ManualPositionResponse, status_code=201)
def add_manual_position(
    body: ManualPositionCreate,
    offline: bool = False,
) -> ManualPositionResponse:
    want = body.ticker.strip().upper()
    save_manual_position(want, pct_nav=body.portfolio_weight_pct / 100.0)
    set_thesis(want, body.thesis)
    state = recompute_one_with_refresh(
        want,
        offline=offline,
        compute_source="manual_single",
    )
    return ManualPositionResponse(
        position=_summary(_holding_or_404(want)),
        refresh=state,
    )


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

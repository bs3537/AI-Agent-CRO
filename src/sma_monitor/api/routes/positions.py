"""Position routes (Workstream 5).

Wraps the existing pipeline functions — no new business logic:
  GET    /api/positions                      grid (P&L, %NAV, catalyst, decision)
  GET    /api/positions/{ticker}             detail (scores, red-team, files)
  PUT    /api/positions/{ticker}/thesis      edit thesis (sidecar.set_thesis)
  POST   /api/positions/{ticker}/files       upload a thesis doc (uploads.save_upload)
  DELETE /api/positions/{ticker}/files/{id}  remove an uploaded doc
  POST   /api/positions/{ticker}/recompute   run the decision engine (bg, or ?wait)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from ...decision.engine import run_decisions
from ...decision.store import latest_decision
from ...portfolio.joined import latest_joined
from ...portfolio.schema import Holding
from ...portfolio.sidecar import set_thesis
from ...portfolio.store import latest_positions
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
    FileOut,
    PositionDetail,
    PositionSummary,
    PositionsResponse,
    RecomputeResponse,
    RedTeamOut,
    ScoreOut,
    ThesisUpdate,
)

router = APIRouter(prefix="/api/positions", tags=["positions"])


# Parse a position_decisions row into the wire shape (drivers stored as JSON).
def _decision_out(row) -> DecisionOut | None:
    if row is None:
        return None
    try:
        drivers = json.loads(row["drivers"] or "[]")
    except (json.JSONDecodeError, TypeError):
        drivers = []
    return DecisionOut(
        verdict=row["verdict"], color=row["color"], note=row["note"],
        drivers=drivers, confidence=row["confidence"],
        model_used=row["model_used"], decided_at=row["decided_at"],
    )


# Build the grid-row summary for one holding: economics + nearest catalyst +
# latest decision + uploaded-file count.
def _summary(h: Holding) -> PositionSummary:
    open_pnl = pnl_pct = None
    if h.cost_basis is not None:
        open_pnl = h.market_value - h.cost_basis
        if h.cost_basis:
            pnl_pct = open_pnl / h.cost_basis
    return PositionSummary(
        ticker=h.ticker, company_name=h.company_name, stage=h.stage,
        conviction_tier=int(h.conviction_tier), qty=h.qty,
        market_value=h.market_value, cost_basis=h.cost_basis, pct_nav=h.pct_nav,
        open_pnl=open_pnl, pnl_pct=pnl_pct,
        nearest_catalyst_days=h.nearest_catalyst_days,
        has_overdue_catalyst=h.has_overdue_catalyst,
        thesis=h.thesis, n_files=len(list_files(h.ticker)),
        decision=_decision_out(latest_decision(h.ticker)),
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
async def upload_file(ticker: str, file: UploadFile = File(...)) -> FileOut:
    want = ticker.strip().upper()
    if want not in _position_tickers():
        raise HTTPException(status_code=404, detail=f"no held position for {want}")
    content = await file.read()
    try:
        rec = save_upload(want, file.filename or "upload", content)
    except UploadError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    return FileOut(**{k: rec[k] for k in
                      ("event_id", "filename", "content_type", "n_chars", "byte_size", "uploaded_at")})


# DELETE /api/positions/{ticker}/files/{event_id} — remove an uploaded doc.
@router.delete("/{ticker}/files/{event_id}", status_code=204)
def remove_file(ticker: str, event_id: str) -> None:
    if not delete_file(event_id):
        raise HTTPException(status_code=404, detail="file not found")


# POST /api/positions/{ticker}/recompute — run the decision engine for one
# ticker. Background by default; ?wait=true runs inline and returns the result.
@router.post("/{ticker}/recompute", response_model=RecomputeResponse)
def recompute(
    ticker: str,
    background: BackgroundTasks,
    wait: bool = False,
    offline: bool = False,
) -> RecomputeResponse:
    want = ticker.strip().upper()
    _holding_or_404(want)  # 404 if not a current holding
    if wait:
        run_decisions(only_ticker=want, force=True, offline=offline)
        return RecomputeResponse(ticker=want, scheduled=False,
                                 decision=_decision_out(latest_decision(want)))
    background.add_task(run_decisions, only_ticker=want, force=True, offline=offline)
    return RecomputeResponse(ticker=want, scheduled=True, decision=None)

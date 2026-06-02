"""Portfolio management routes — IBKR Flex pull trigger."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...config import settings
from ...portfolio.flex import FlexError, fetch_statement, parse_positions
from ...portfolio.ir_urls import populate_ir_urls_for_tickers
from ...portfolio.store import init_portfolio_schema, latest_positions, save_pull

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# Response schema for a completed IBKR Flex pull.
class PullResponse(BaseModel):
    pull_id: str
    nav: float
    ticker_count: int
    pulled_at: str
    new_tickers: list[str]
    removed_tickers: list[str]


# POST /api/portfolio/pull — trigger a live IBKR Flex pull and persist the
# results. Returns a diff of new vs removed tickers relative to the prior pull
# so the caller can surface the change to the user.
@router.post("/pull", response_model=PullResponse)
async def pull_from_ibkr() -> PullResponse:
    # Verify IBKR credentials are configured before attempting the pull.
    missing = settings.missing_for(1)
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"IBKR credentials not configured: {', '.join(missing)}",
        )

    # Capture the previous pull's tickers so we can report the diff.
    prev_positions, _ = latest_positions()
    prev_tickers = {p.ticker for p in prev_positions}

    # Fetch the Flex statement in a thread (blocking network I/O).
    try:
        raw = await asyncio.to_thread(
            fetch_statement,
            settings.ibkr_flex_token,
            settings.ibkr_flex_query_id,
        )
    except FlexError as e:
        raise HTTPException(status_code=502, detail=f"IBKR Flex fetch failed: {e}") from e

    # Parse XML → positions.
    try:
        positions, nav = await asyncio.to_thread(
            parse_positions, raw.xml, pulled_at=raw.pulled_at
        )
    except FlexError as e:
        raise HTTPException(status_code=502, detail=f"IBKR Flex parse failed: {e}") from e

    # Persist to DB (idempotent on pulled_at/nav).
    pull_id = await asyncio.to_thread(
        save_pull,
        positions,
        nav=nav,
        pulled_at=raw.pulled_at,
        source="ibkr_flex",
        raw_xml=None,
    )

    # Populate IR URLs for newly discovered tickers (best-effort, non-blocking).
    new_ticker_list = [p.ticker for p in positions]
    await asyncio.to_thread(
        populate_ir_urls_for_tickers,
        new_ticker_list,
        create_missing=True,
    )

    # Compute the diff: which tickers are brand-new, which were removed.
    new_tickers_set = {p.ticker for p in positions}
    new_tickers = sorted(new_tickers_set - prev_tickers)
    removed_tickers = sorted(prev_tickers - new_tickers_set)

    return PullResponse(
        pull_id=pull_id,
        nav=nav,
        ticker_count=len(positions),
        pulled_at=raw.pulled_at.isoformat(),
        new_tickers=new_tickers,
        removed_tickers=removed_tickers,
    )

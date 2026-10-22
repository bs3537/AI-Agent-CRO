"""FastAPI application (Workstream 5).

Thin REST layer over the existing sma_monitor functions — the primary surface
the React dashboard talks to. In production the built frontend bundle
(frontend/dist) is served as static files from the same origin; in dev the
Vite server (localhost:5173) hits this API cross-origin, so those origins are
allowed by CORS.

Run:  python -m sma_monitor.api   (or: uvicorn sma_monitor.api.app:app)
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import settings
from ..db import init_db
from ..paths import ensure_dirs
from .routes import chat, jobs, portfolio, positions, quotes, status

_log = logging.getLogger(__name__)

# Default dev origins (Vite). Override with SMA_API_CORS_ORIGINS (comma-sep).
_DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

# Scheduled automatic IBKR position sync — 6:00 AM Eastern every day (DST-aware)
# so holdings and weights on the dashboard are fresh each morning.
_IBKR_SYNC_TIME_ET = dtime(6, 0)
_ET = ZoneInfo("America/New_York")


def _seconds_until_next(target: dtime) -> float:
    """Seconds from now until the next occurrence of *target* ET wall-clock time."""
    now_et = datetime.now(tz=_ET)
    candidate = now_et.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now_et:
        candidate += timedelta(days=1)
    return (candidate - now_et).total_seconds()


async def _run_ibkr_sync() -> None:
    """Pull holdings + weights from the IBKR Flex query and persist them."""
    from ..config import settings
    from ..orchestrator.job_status import record_job_run
    from ..portfolio.flex import FlexError, fetch_statement, parse_positions
    from ..portfolio.ir_urls import populate_ir_urls_for_tickers
    from ..portfolio.store import (
        backfill_manual_positions_from_ibkr,
        latest_positions,
        save_pull,
    )

    missing = settings.missing_for(1)
    if missing:
        _log.warning(
            "ibkr_sync_skipped",
            extra={"reason": "missing_credentials", "keys": missing},
        )
        await asyncio.to_thread(
            record_job_run, "ibkr_pull", False, f"missing credentials: {', '.join(missing)}"
        )
        return

    _log.info("ibkr_sync_start")
    try:
        prev_positions, _ = await asyncio.to_thread(latest_positions)
        prev_tickers = {p.ticker for p in prev_positions}
        raw = await asyncio.to_thread(
            fetch_statement, settings.ibkr_flex_token, settings.ibkr_flex_query_id
        )
        positions_list, nav = await asyncio.to_thread(
            parse_positions, raw.xml, pulled_at=raw.pulled_at
        )
        pull_id = await asyncio.to_thread(
            save_pull,
            positions_list,
            nav=nav,
            pulled_at=raw.pulled_at,
            source="ibkr_flex_scheduled",
            raw_xml=None,
        )
        await asyncio.to_thread(
            record_job_run, "ibkr_pull", True, f"{len(positions_list)} positions, NAV {nav:,.0f}"
        )
        _log.info(
            "ibkr_sync_done",
            extra={"pull_id": pull_id, "nav": nav, "ticker_count": len(positions_list)},
        )
        filled = await asyncio.to_thread(
            backfill_manual_positions_from_ibkr, positions_list
        )
        if filled:
            _log.info("ibkr_sync_backfilled_manual", extra={"count": filled})
        tickers = [p.ticker for p in positions_list]
        asyncio.create_task(
            asyncio.to_thread(populate_ir_urls_for_tickers, tickers, create_missing=True)
        )
        new_tickers = sorted(set(tickers) - prev_tickers) if prev_tickers else []
        if new_tickers:
            _log.info("ibkr_new_holdings_detected", extra={"tickers": new_tickers})
            asyncio.create_task(asyncio.to_thread(_research_new_holdings, new_tickers))
    except FlexError as exc:
        _log.error("ibkr_sync_failed", extra={"err": str(exc)})
        await asyncio.to_thread(record_job_run, "ibkr_pull", False, str(exc))
    except Exception as exc:
        _log.exception("ibkr_sync_unexpected_error", extra={"err": str(exc)})
        await asyncio.to_thread(record_job_run, "ibkr_pull", False, repr(exc))


# Refresh every FMP-sourced dashboard input for the current holdings: profile
# metrics, daily closes (trend + 20-EMA are derived from these), and sell-side
# consensus targets (PT upside is computed against the latest close).
def _refresh_market_data() -> None:
    from ..analyst_targets.service import refresh_fmp_targets
    from ..news.fmp_client import refresh_for_holdings, refresh_prices_for_holdings

    from ..orchestrator.job_status import record_job_run

    key = settings.fmp_api_key
    if not key:
        _log.warning("market_data_refresh_skipped", extra={"reason": "FMP_API_KEY missing"})
        record_job_run("fmp_market_data", False, "FMP_API_KEY missing")
        return
    steps = (
        ("fmp_metrics", lambda: refresh_for_holdings(api_key=key)),
        ("fmp_prices", lambda: refresh_prices_for_holdings(api_key=key)),
        ("fmp_targets", lambda: refresh_fmp_targets(api_key=key)),
    )
    failures: list[str] = []
    for name, fn in steps:
        try:
            result = fn()
            _log.info("market_data_step_done", extra={"step": name, "result": str(result)[:300]})
        except Exception as exc:
            _log.exception("market_data_step_failed", extra={"step": name, "err": str(exc)})
            failures.append(f"{name}: {exc}")
    record_job_run(
        "fmp_market_data", not failures, "; ".join(failures) or "metrics, prices, targets refreshed"
    )


async def _run_daily_refresh() -> None:
    """6 AM job: IBKR holdings + weights first, then FMP prices/targets for them."""
    from .routes.positions import invalidate_positions_cache

    await _run_ibkr_sync()
    await asyncio.to_thread(_refresh_market_data)
    invalidate_positions_cache()


def _research_new_holdings(tickers: list[str]) -> None:
    """Web-search a preliminary thesis and the catalyst calendar for new holdings."""
    from ..news.catalyst_outlook import refresh_catalyst_outlooks_for_holdings
    from ..orchestrator.preliminary_thesis import run_preliminary_thesis_workflow

    try:
        run_preliminary_thesis_workflow(
            tickers=tickers,
            refresh_inputs=True,
            compute_source="web_search_new_holding",
        )
        refresh_catalyst_outlooks_for_holdings(tickers=tickers)
    except Exception as exc:
        _log.exception("new_holding_research_failed", extra={"err": str(exc)})


async def _ibkr_sync_loop() -> None:
    """Background task: pull IBKR positions at 6 AM ET daily.

    On startup it also catches up: if today's 6 AM pull was missed (restart,
    redeploy), it pulls immediately instead of waiting until tomorrow.
    """
    from ..portfolio.store import latest_pull_at

    now_et = datetime.now(tz=_ET)
    today_sync = now_et.replace(
        hour=_IBKR_SYNC_TIME_ET.hour, minute=_IBKR_SYNC_TIME_ET.minute,
        second=0, microsecond=0,
    )
    if now_et >= today_sync:
        last = await asyncio.to_thread(latest_pull_at)
        if last is None or last.astimezone(_ET) < today_sync:
            _log.info("ibkr_sync_catch_up")
            await _run_daily_refresh()

    while True:
        wait = _seconds_until_next(_IBKR_SYNC_TIME_ET)
        next_et = datetime.now(tz=_ET) + timedelta(seconds=wait)
        _log.info(
            "ibkr_sync_scheduled",
            extra={"next_sync_et": next_et.strftime("%Y-%m-%d %H:%M ET")},
        )
        await asyncio.sleep(wait)
        await _run_daily_refresh()


# Seconds until the next Sunday 6:00 PM Eastern (DST-aware).
def _seconds_until_next_sunday_6pm() -> float:
    now_et = datetime.now(tz=_ET)
    candidate = now_et.replace(hour=18, minute=0, second=0, microsecond=0)
    candidate += timedelta(days=(6 - now_et.weekday()) % 7)
    if candidate <= now_et:
        candidate += timedelta(days=7)
    return (candidate - now_et).total_seconds()


async def _catalyst_refresh_loop() -> None:
    """Background task: web-search catalysts for all holdings every Sunday 6 PM ET."""
    from ..news.catalyst_outlook import refresh_catalyst_outlooks_for_holdings
    from ..orchestrator.job_status import record_job_run

    while True:
        wait = _seconds_until_next_sunday_6pm()
        _log.info("catalyst_refresh_scheduled", extra={"in_seconds": int(wait)})
        await asyncio.sleep(wait)
        _log.info("catalyst_refresh_start")
        try:
            state = await asyncio.to_thread(refresh_catalyst_outlooks_for_holdings)
            await asyncio.to_thread(
                record_job_run, "catalyst_search", state.get("errors", 0) == 0,
                f"{state.get('updated', 0)} updated, {state.get('errors', 0)} failed",
            )
            from .routes.positions import invalidate_positions_cache

            invalidate_positions_cache()
            _log.info("catalyst_refresh_done", extra={"state": str(state)[:300]})
        except Exception as exc:
            _log.exception("catalyst_refresh_failed", extra={"err": str(exc)})
            await asyncio.to_thread(record_job_run, "catalyst_search", False, repr(exc))


# Resolve allowed CORS origins from the environment, falling back to the Vite
# dev server. "*" (any value containing it) opens CORS to all origins.
def _cors_origins() -> list[str]:
    raw = os.environ.get("SMA_API_CORS_ORIGINS")
    if not raw:
        return _DEFAULT_ORIGINS
    return [o.strip() for o in raw.split(",") if o.strip()]


# Locate the built frontend bundle to serve as static files, if present.
# SMA_FRONTEND_DIST overrides; default is <repo>/frontend/dist.
def _frontend_dist() -> Path | None:
    env = os.environ.get("SMA_FRONTEND_DIST")
    candidate = Path(env) if env else Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


# Startup/shutdown lifespan: ensure the data dirs + universal events table
# exist before serving; per-phase tables self-initialize on first access.
@asynccontextmanager
async def _lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    _init_served_phase_schemas()
    tasks: list[asyncio.Task] = []
    # The daily IBKR sync runs only in the published deployment (or combined
    # role) so the dev workspace never double-pulls into the shared database.
    if settings.is_combined_role() or os.environ.get("REPLIT_DEPLOYMENT"):
        tasks.append(asyncio.create_task(_ibkr_sync_loop()))
        tasks.append(asyncio.create_task(_catalyst_refresh_loop()))
    else:
        _log.info("api_background_loops_disabled", extra={"role": settings.deployment_role()})
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t


def _init_served_phase_schemas() -> None:
    # The API reads across phase tables even when a fresh environment has not
    # run a full collect cycle yet. Initialize all served schemas at startup.
    from ..analyst_targets.store import init_analyst_target_schema
    from ..decision.store import init_decision_schema
    from ..news.catalyst_outlook import init_catalyst_schema
    from ..news.fmp_client import init_fmp_schema
    from ..news.store import init_news_schema
    from ..orchestrator.store import init_orchestrator_schema
    from ..portfolio.sidecar import init_sidecar_schema
    from ..portfolio.store import init_portfolio_schema
    from ..portfolio.uploads import init_uploads_schema
    from ..red_team.store import init_red_team_schema
    from ..scorer.store import init_scores_schema

    init_portfolio_schema()
    init_sidecar_schema()
    init_uploads_schema()
    init_news_schema()
    init_fmp_schema()
    init_analyst_target_schema()
    init_catalyst_schema()
    init_scores_schema()
    init_red_team_schema()
    init_orchestrator_schema()
    init_decision_schema()


# Application factory. Wires CORS, the API routers, a health check, the
# startup schema bootstrap, and (when built) the static frontend.
def create_app() -> FastAPI:
    app = FastAPI(title="SMA Monitor API", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Liveness probe (separate from the heavier /api/status snapshot).
    @app.get("/api/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(positions.router)
    app.include_router(portfolio.router)
    app.include_router(chat.router)
    app.include_router(status.router)
    app.include_router(jobs.router)
    app.include_router(quotes.router)

    # Serve the built SPA at the root when it exists (production). html=True
    # makes client-side routing fall back to index.html.
    dist = _frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


# Module-level instance for `uvicorn sma_monitor.api.app:app`.
app = create_app()

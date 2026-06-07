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
from .routes import chat, portfolio, positions, quotes, status

_log = logging.getLogger(__name__)

# Default dev origins (Vite). Override with SMA_API_CORS_ORIGINS (comma-sep).
_DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

# Scheduled automatic IBKR position sync — midnight Eastern every day so
# positions are fresh before the 6 AM recompute and 9:15 AM email.
_IBKR_SYNC_TIME_ET = dtime(0, 0)
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


async def _scheduler_loop() -> None:
    """Background task: fire the four daily orchestrator cycles at their scheduled ET times.

    Mirrors orchestrator/schedule.py run_loop() but runs inside the API process
    as an asyncio background task so it works in Replit deployments where systemd
    and cron are unavailable.

    Firing times (Eastern, DST-aware):
      06:00  — morning thesis recompute (smart news/SEC/EMA/P&L gate)
      09:15  — morning thesis email delivery
      18:00  — evening collect cycle (positions + news + score + decisions)
      21:00  — evening dispatch cycle (digest assembly + delivery)
    """
    from ..orchestrator.pipeline import (
        run_collect_cycle,
        run_dispatch_cycle,
        run_morning_thesis_delivery_cycle,
        run_morning_thesis_recompute_cycle,
    )
    from ..orchestrator.schedule import (
        COLLECT_TIME_ET,
        DISPATCH_TIME_ET,
        MORNING_EMAIL_TIME_ET,
        MORNING_RECOMPUTE_TIME_ET,
        next_firing_at,
    )

    firings = [
        (
            MORNING_RECOMPUTE_TIME_ET,
            "morning_thesis_recompute",
            run_morning_thesis_recompute_cycle,
        ),
        (
            MORNING_EMAIL_TIME_ET,
            "morning_thesis_email",
            run_morning_thesis_delivery_cycle,
        ),
        (COLLECT_TIME_ET, "collect", run_collect_cycle),
        (DISPATCH_TIME_ET, "dispatch", run_dispatch_cycle),
    ]

    _log.info("scheduler_loop_started")

    while True:
        now_utc = datetime.now(tz=ZoneInfo("UTC"))
        target_dt, cycle_name, cycle_fn = min(
            ((next_firing_at(t, now_utc=now_utc), name, fn) for t, name, fn in firings),
            key=lambda x: x[0],
        )
        sleep_secs = max(60, (target_dt - now_utc).total_seconds())
        _log.info(
            "scheduler_sleeping",
            extra={
                "next_cycle": cycle_name,
                "next_fire_utc": target_dt.isoformat(),
                "sleep_secs": int(sleep_secs),
            },
        )
        await asyncio.sleep(sleep_secs)

        _log.info("scheduler_cycle_start", extra={"cycle": cycle_name})
        try:
            await asyncio.to_thread(cycle_fn)
        except Exception as exc:
            _log.exception("scheduler_cycle_error", extra={"cycle": cycle_name, "err": str(exc)})
        else:
            _log.info("scheduler_cycle_done", extra={"cycle": cycle_name})


async def _ibkr_sync_loop() -> None:
    """Background task: pull IBKR positions at 8 PM ET daily, fire-and-forget."""
    from ..config import settings
    from ..portfolio.flex import FlexError, fetch_statement, parse_positions
    from ..portfolio.ir_urls import populate_ir_urls_for_tickers
    from ..portfolio.store import save_pull

    while True:
        wait = _seconds_until_next(_IBKR_SYNC_TIME_ET)
        next_et = datetime.now(tz=_ET) + timedelta(seconds=wait)
        _log.info(
            "ibkr_sync_scheduled",
            extra={"next_sync_et": next_et.strftime("%Y-%m-%d %H:%M ET")},
        )
        await asyncio.sleep(wait)

        missing = settings.missing_for(1)
        if missing:
            _log.warning(
                "ibkr_sync_skipped",
                extra={"reason": "missing_credentials", "keys": missing},
            )
            continue

        _log.info("ibkr_sync_start")
        try:
            raw = await asyncio.to_thread(
                fetch_statement,
                settings.ibkr_flex_token,
                settings.ibkr_flex_query_id,
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
            _log.info(
                "ibkr_sync_done",
                extra={
                    "pull_id": pull_id,
                    "nav": nav,
                    "ticker_count": len(positions_list),
                },
            )
            from ..portfolio.store import backfill_manual_positions_from_ibkr
            filled = await asyncio.to_thread(
                backfill_manual_positions_from_ibkr, positions_list
            )
            if filled:
                _log.info("ibkr_sync_backfilled_manual", extra={"count": filled})
            tickers = [p.ticker for p in positions_list]
            asyncio.create_task(
                asyncio.to_thread(
                    populate_ir_urls_for_tickers, tickers, create_missing=True
                )
            )
        except FlexError as exc:
            _log.error("ibkr_sync_failed", extra={"err": str(exc)})
        except Exception as exc:
            _log.exception("ibkr_sync_unexpected_error", extra={"err": str(exc)})


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
    if settings.is_combined_role():
        tasks.append(asyncio.create_task(_ibkr_sync_loop()))
        tasks.append(asyncio.create_task(_scheduler_loop()))
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
    from ..decision.store import init_decision_schema
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
    app.include_router(quotes.router)

    # Serve the built SPA at the root when it exists (production). html=True
    # makes client-side routing fall back to index.html.
    dist = _frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


# Module-level instance for `uvicorn sma_monitor.api.app:app`.
app = create_app()

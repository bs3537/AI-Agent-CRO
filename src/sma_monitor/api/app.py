"""FastAPI application (Workstream 5).

Thin REST layer over the existing sma_monitor functions — the primary surface
the React dashboard talks to. In production the built frontend bundle
(frontend/dist) is served as static files from the same origin; in dev the
Vite server (localhost:5173) hits this API cross-origin, so those origins are
allowed by CORS.

Run:  python -m sma_monitor.api   (or: uvicorn sma_monitor.api.app:app)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..db import init_db
from ..paths import ensure_dirs
from .routes import chat, portfolio, positions, status

# Default dev origins (Vite). Override with SMA_API_CORS_ORIGINS (comma-sep).
_DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


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
    yield


def _init_served_phase_schemas() -> None:
    # The API reads across phase tables even when a fresh environment has not
    # run a full collect cycle yet. Initialize all served schemas at startup.
    from ..decision.store import init_decision_schema
    from ..news.fmp_client import init_fmp_schema
    from ..news.store import init_news_schema
    from ..orchestrator.store import init_orchestrator_schema
    from ..portfolio.store import init_portfolio_schema
    from ..portfolio.uploads import init_uploads_schema
    from ..red_team.store import init_red_team_schema
    from ..scorer.store import init_scores_schema

    init_portfolio_schema()
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

    # Serve the built SPA at the root when it exists (production). html=True
    # makes client-side routing fall back to index.html.
    dist = _frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


# Module-level instance for `uvicorn sma_monitor.api.app:app`.
app = create_app()

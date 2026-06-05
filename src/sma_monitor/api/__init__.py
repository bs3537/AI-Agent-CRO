"""Workstream 5 — FastAPI backend.

A thin REST layer wrapping the existing phase 1–7 + decision-engine functions
so the React dashboard (W6) can read positions, P&L, and thesis-drift
decisions; edit theses; upload thesis documents; and trigger recomputes. The
app factory lives in `app.py` (`create_app()` / module-level `app`); run it
with `python -m sma_monitor.api` or `uvicorn sma_monitor.api.app:app`.

Only `create_app` is re-exported lazily here — importing `app.py` at package
initialization time pulls in routes, which can create circular imports for
non-API modules that only need `sma_monitor.api.schemas`.
"""
from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


# Lazily expose the application factory without importing route modules early.
def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Workstream 5 — FastAPI backend.

A thin REST layer wrapping the existing phase 1–7 + decision-engine functions
so the React dashboard (W6) can read positions, P&L, and thesis-drift
decisions; edit theses; upload thesis documents; and trigger recomputes. The
app factory lives in `app.py` (`create_app()` / module-level `app`); run it
with `python -m sma_monitor.api` or `uvicorn sma_monitor.api.app:app`.

Only `create_app` is re-exported here — re-exporting the `app` instance would
shadow the `app` submodule name and break `sma_monitor.api.app` imports.
"""
from .app import create_app

__all__ = ["create_app"]

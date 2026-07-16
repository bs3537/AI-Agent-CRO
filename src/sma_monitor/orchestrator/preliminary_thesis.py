"""Orchestration for researched preliminary-thesis generation."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..analyst_targets.service import refresh_tipranks_targets
from ..config import settings
from ..news.fmp_client import refresh_for_holdings
from ..portfolio.draft_thesis import bootstrap_ai_draft_sidecars
from ..portfolio.ir_urls import populate_ir_urls_for_tickers
from ..portfolio.store import latest_positions

log = logging.getLogger("sma_monitor.orchestrator.preliminary_thesis")


def run_preliminary_thesis_workflow(
    *,
    tickers: Sequence[str] | None = None,
    limit: int | None = None,
    upgrade_existing_ai: bool = False,
    refresh_inputs: bool = False,
    compute_source: str = "hermes_preliminary_thesis",
    provider=None,
) -> dict[str, Any]:
    """Refresh targeted context and generate a PM-safe batch of AI drafts."""
    positions, _pulled_at = latest_positions()
    held = {position.ticker for position in positions}
    wanted = sorted(
        {
            ticker.strip().upper()
            for ticker in (tickers or held)
            if ticker and ticker.strip() and ticker.strip().upper() in held
        }
    )
    state: dict[str, Any] = {
        "requested_tickers": wanted,
        "missing_positions": sorted(
            {
                ticker.strip().upper()
                for ticker in (tickers or [])
                if ticker and ticker.strip() and ticker.strip().upper() not in held
            }
        ),
        "refresh": {},
    }
    if not wanted:
        state["drafts"] = {
            "created": 0,
            "upgraded_existing_ai": 0,
            "failed": [],
        }
        return state

    if refresh_inputs:
        state["refresh"]["fmp"] = _capture(
            lambda: refresh_for_holdings(
                api_key=settings.fmp_api_key,
                tickers=wanted,
            )
        )
        state["refresh"]["ir_urls"] = _capture(
            lambda: populate_ir_urls_for_tickers(wanted, create_missing=False)
        )
        state["refresh"]["tipranks"] = _capture(
            lambda: refresh_tipranks_targets(tickers=wanted)
        )

    state["drafts"] = bootstrap_ai_draft_sidecars(
        positions=positions,
        provider=provider,
        compute_source=compute_source,
        limit=limit,
        only_tickers=wanted,
        upgrade_existing_ai=upgrade_existing_ai,
    )
    return state


def _capture(fn) -> dict[str, Any]:
    try:
        result = fn()
        return result if isinstance(result, dict) else {"result": result}
    except Exception as exc:  # noqa: BLE001 - draft research can proceed with cached inputs.
        message = str(exc)[:300]
        log.warning("preliminary_thesis_input_refresh_failed", extra={"error": message})
        return {"status": "failed", "error": message}

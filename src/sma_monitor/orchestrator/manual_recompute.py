"""Manual dashboard recompute orchestration.

The dashboard buttons are operator overrides, not just cache refreshes. They
should first pull fresh ticker evidence into the DB, then process that evidence
through scorer/red-team, and only then ask the decision LLM for the final grade.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from ..config import settings
from ..decision.engine import run_decisions
from ..news.fmp_client import refresh_for_holdings, refresh_prices_for_holdings
from ..news.pipeline import poll as news_poll
from ..news.pipeline import poll_literature, poll_sec
from ..portfolio.joined import latest_joined
from ..red_team.pipeline import run_red_team
from ..scorer.pipeline import score_unscored

log = logging.getLogger("sma_monitor.orchestrator.manual_recompute")

# Manual checks should be broad enough to catch FDA/earnings/news updates, but
# bounded so one button press does not become a full research crawl.
MANUAL_NEWS_LOOKBACK_HOURS = 24 * 7
MANUAL_NEWS_RESULTS = 3
MANUAL_LITERATURE_RESULTS = 2
MANUAL_SEC_RESULTS = 3


def recompute_one_with_refresh(
    ticker: str,
    *,
    offline: bool = False,
    compute_source: str = "manual_single",
) -> dict:
    """Refresh one ticker's evidence, score/red-team it, then recompute rating."""
    want = ticker.upper()
    state = _base_state([want], offline=offline)
    state["refresh"] = _refresh_evidence([want], offline=offline)
    state["scoring"] = score_unscored(
        api_key=settings.anthropic_api_key,
        offline=offline,
        ticker=want,
    )
    state["red_team"] = run_red_team(
        api_key=settings.anthropic_api_key,
        offline=offline,
        ticker=want,
    )
    state["decisions"] = run_decisions(
        only_ticker=want,
        force=True,
        offline=offline,
        compute_source=compute_source,
    )
    state["finished_at"] = datetime.now(UTC).isoformat()
    log.info("manual_recompute_one_done", extra={"ticker": want, "summary": state})
    return state


def recompute_all_with_refresh(*, offline: bool = False, force: bool = True) -> dict:
    """Refresh all current holdings, score/red-team them, then recompute ratings."""
    holdings, missing, _pulled_at = latest_joined()
    tickers = [h.ticker for h in holdings]
    state = _base_state(tickers, offline=offline)
    state["force"] = force
    state["missing_sidecars"] = missing
    state["refresh"] = _refresh_evidence(tickers, offline=offline)
    state["scoring"] = score_unscored(
        api_key=settings.anthropic_api_key,
        offline=offline,
    )
    state["red_team"] = run_red_team(
        api_key=settings.anthropic_api_key,
        offline=offline,
    )
    state["decisions"] = run_decisions(
        force=force,
        offline=offline,
        compute_source="manual_all",
    )
    state["finished_at"] = datetime.now(UTC).isoformat()
    log.info("manual_recompute_all_done", extra={"summary": state})
    return state


def _base_state(tickers: Iterable[str], *, offline: bool) -> dict:
    return {
        "started_at": datetime.now(UTC).isoformat(),
        "offline": offline,
        "tickers": [t.upper() for t in tickers],
    }


def _refresh_evidence(tickers: list[str], *, offline: bool) -> dict:
    """Best-effort live refresh. Errors are recorded, not allowed to sink rating."""
    state: dict = {}
    if offline:
        return {"status": "skipped_offline"}

    if len(tickers) == 1:
        ticker = tickers[0]
        state["news"] = _capture(
            "news",
            lambda: news_poll(
                api_key=settings.exa_api_key,
                filter_ticker=ticker,
                num_results=MANUAL_NEWS_RESULTS,
                lookback_hours=MANUAL_NEWS_LOOKBACK_HOURS,
            ),
        )
        state["literature"] = _capture(
            "literature",
            lambda: poll_literature(
                api_key=settings.semantic_scholar_api_key,
                filter_ticker=ticker,
                num_results=MANUAL_LITERATURE_RESULTS,
            ),
        )
        state["sec"] = _capture(
            "sec",
            lambda: poll_sec(
                user_agent=settings.sec_edgar_user_agent,
                filter_ticker=ticker,
                num_results=MANUAL_SEC_RESULTS,
            ),
        )
    else:
        state["news"] = _capture(
            "news",
            lambda: news_poll(
                api_key=settings.exa_api_key,
                num_results=MANUAL_NEWS_RESULTS,
                lookback_hours=MANUAL_NEWS_LOOKBACK_HOURS,
            ),
        )
        state["literature"] = _capture(
            "literature",
            lambda: poll_literature(
                api_key=settings.semantic_scholar_api_key,
                num_results=MANUAL_LITERATURE_RESULTS,
            ),
        )
        state["sec"] = _capture(
            "sec",
            lambda: poll_sec(
                user_agent=settings.sec_edgar_user_agent,
                num_results=MANUAL_SEC_RESULTS,
            ),
        )

    state["financials"] = _capture(
        "financials",
        lambda: refresh_for_holdings(api_key=settings.fmp_api_key, tickers=tickers),
    )
    state["prices"] = _capture(
        "prices",
        lambda: refresh_prices_for_holdings(api_key=settings.fmp_api_key, tickers=tickers),
    )
    return state


def _capture(stage: str, fn) -> dict:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - manual recompute is best-effort.
        log.warning("manual_refresh_stage_failed", extra={"stage": stage, "err": str(e)})
        return {"status": "skipped", "reason": str(e)[:240]}

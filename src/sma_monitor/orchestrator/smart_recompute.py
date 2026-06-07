"""Morning smart recompute orchestration.

The scheduled morning job monitors every holding, then spends LLM time only on
positions with a fresh risk trigger. Manual recompute remains the operator
override that force-runs a full refresh for one or all holdings.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from ..config import settings
from ..decision.engine import run_decisions
from ..decision.technicals import technical_state
from ..news.fmp_client import (
    latest_price_series,
    refresh_for_holdings,
    refresh_prices_for_holdings,
)
from ..news.pipeline import poll_company_news, poll_sec
from ..news.store import article_ids_by_ticker_since
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from ..red_team.pipeline import run_red_team
from ..scorer.pipeline import score_unscored
from .manual_recompute import _capture

log = logging.getLogger("sma_monitor.orchestrator.smart_recompute")

MORNING_NEWS_LOOKBACK_HOURS = 30
MORNING_COMPANY_NEWS_RESULTS = 5
MORNING_SEC_RESULTS = 5
OPEN_LOSS_TRIGGER_PCT = -0.10
BELOW_EMA20_STATES = {"below_ema20", "extended_below_ema20"}
SMART_COMPUTE_SOURCE = "scheduler_morning_smart"

PositionRefreshFn = Callable[..., dict[str, Any]]


def run_smart_morning_recompute(
    *,
    offline: bool = False,
    refresh_positions_fn: PositionRefreshFn | None = None,
    compute_source: str = SMART_COMPUTE_SOURCE,
) -> dict:
    """Refresh daily signals for all holdings and recompute only triggered names."""
    started_at = datetime.now(UTC)
    state: dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "offline": offline,
        "mode": "smart_morning",
        "compute_source": compute_source,
        "triggers": [],
        "quiet": [],
    }

    if refresh_positions_fn is not None and not offline:
        state["positions"] = _capture("positions", lambda: refresh_positions_fn(force=True))
    if not offline:
        from ..portfolio.draft_thesis import bootstrap_ai_draft_sidecars

        state["new_position_drafts"] = _capture(
            "new_position_drafts",
            lambda: bootstrap_ai_draft_sidecars(compute_source="scheduler_new_position_draft"),
        )

    holdings, missing, pulled_at = latest_joined()
    tickers = [h.ticker for h in holdings]
    state["holdings"] = len(holdings)
    state["missing_sidecars"] = missing
    state["pulled_at"] = pulled_at.isoformat() if pulled_at else None
    if not holdings:
        state["refresh"] = {"status": "skipped", "reason": "no_holdings"}
        state["decisions"] = {"decided": 0, "skipped": 0, "errors": 0, "holdings": 0}
        state["finished_at"] = datetime.now(UTC).isoformat()
        return state

    if offline:
        news_new: dict[str, int] = {}
        sec_new: dict[str, int] = {}
        state["refresh"] = {"status": "skipped_offline"}
    else:
        refresh = _refresh_all_daily_signals(tickers)
        state["refresh"] = refresh
        news_new = _new_counts(refresh.get("company_news", {}))
        sec_new = _new_counts(refresh.get("sec", {}))
        fresh_article_ids_by_ticker = article_ids_by_ticker_since(
            tickers=tickers,
            since=started_at,
        )
        state["fresh_evidence"] = {
            "since": started_at.isoformat(),
            "by_ticker": {t: len(ids) for t, ids in fresh_article_ids_by_ticker.items()},
            "total": sum(len(ids) for ids in fresh_article_ids_by_ticker.values()),
        }

    # Re-read holdings after the broker refresh so pct_nav/open P&L reflect the
    # latest snapshot, then triage using the freshly cached price series.
    holdings, missing, pulled_at = latest_joined()
    state["missing_sidecars"] = missing
    state["pulled_at"] = pulled_at.isoformat() if pulled_at else state["pulled_at"]
    triage = triage_holdings(holdings, news_new_by_ticker=news_new, sec_new_by_ticker=sec_new)
    state["triggers"] = triage["triggered"]
    state["quiet"] = triage["quiet"]

    decisions = {"decided": 0, "skipped": 0, "errors": 0, "holdings": len(holdings)}
    per_ticker: dict[str, Any] = {}
    for row in state["triggers"]:
        ticker = row["ticker"]
        per_ticker[ticker] = _recompute_triggered_ticker(
            ticker,
            offline=offline,
            compute_source=compute_source,
            fresh_article_ids=(
                [] if offline else fresh_article_ids_by_ticker.get(ticker, [])
            ),
        )
        decision = per_ticker[ticker].get("decision", {})
        decisions["decided"] += int(decision.get("decided", 0) or 0)
        decisions["skipped"] += int(decision.get("skipped", 0) or 0)
        decisions["errors"] += int(decision.get("errors", 0) or 0)

    state["recomputed_tickers"] = [r["ticker"] for r in state["triggers"]]
    state["per_ticker"] = per_ticker
    state["decisions"] = decisions
    state["finished_at"] = datetime.now(UTC).isoformat()
    log.info("smart_morning_recompute_done", extra={"summary": state})
    return state


def triage_holdings(
    holdings: list[Holding],
    *,
    news_new_by_ticker: dict[str, int],
    sec_new_by_ticker: dict[str, int],
    price_series_by_ticker: dict[str, list[float] | None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return triggered vs quiet holdings using deterministic daily checks."""
    triggered: list[dict[str, Any]] = []
    quiet: list[dict[str, Any]] = []
    for h in holdings:
        row = triage_holding(
            h,
            fresh_news_count=news_new_by_ticker.get(h.ticker, 0),
            fresh_sec_count=sec_new_by_ticker.get(h.ticker, 0),
            price_series=(
                price_series_by_ticker[h.ticker]
                if price_series_by_ticker is not None and h.ticker in price_series_by_ticker
                else latest_price_series(h.ticker)
            ),
        )
        if row["recompute"]:
            triggered.append(row)
        else:
            quiet.append(row)
    return {"triggered": triggered, "quiet": quiet}


def triage_holding(
    holding: Holding,
    *,
    fresh_news_count: int,
    fresh_sec_count: int,
    price_series: list[float] | None,
) -> dict[str, Any]:
    """Classify one holding for the morning smart-recompute gate."""
    pnl_pct = _open_pnl_pct(holding)
    tech = technical_state(price_series)
    recompute_reasons: list[str] = []
    monitor_reasons: list[str] = []

    if fresh_news_count > 0:
        recompute_reasons.append("fresh_company_or_yahoo_news")
    if fresh_sec_count > 0:
        recompute_reasons.append("fresh_sec_filing")
    if pnl_pct is not None and pnl_pct <= OPEN_LOSS_TRIGGER_PCT:
        monitor_reasons.append("open_loss_ge_10pct")
    if tech.technical_state in BELOW_EMA20_STATES:
        monitor_reasons.append(tech.technical_state)

    return {
        "ticker": holding.ticker,
        "recompute": bool(recompute_reasons),
        "reasons": recompute_reasons,
        "monitor_reasons": monitor_reasons,
        "fresh_news_count": fresh_news_count,
        "fresh_sec_count": fresh_sec_count,
        "pct_nav": holding.pct_nav,
        "open_pnl_pct": round(pnl_pct, 6) if pnl_pct is not None else None,
        "technical_state": tech.technical_state,
        "price_vs_ema20_pct": tech.price_vs_ema20_pct,
        "consecutive_below_ema20": tech.consecutive_below_ema20,
    }


def _refresh_all_daily_signals(tickers: list[str]) -> dict[str, Any]:
    refresh: dict[str, Any] = {}
    refresh["company_news"] = _capture(
        "company_news",
        lambda: poll_company_news(
            api_key=settings.exa_api_key,
            num_results=MORNING_COMPANY_NEWS_RESULTS,
            lookback_hours=MORNING_NEWS_LOOKBACK_HOURS,
        ),
    )
    refresh["sec"] = _poll_sec_by_ticker(tickers)
    refresh["financials"] = _capture(
        "financials",
        lambda: refresh_for_holdings(api_key=settings.fmp_api_key, tickers=tickers),
    )
    refresh["prices"] = _capture(
        "prices",
        lambda: refresh_prices_for_holdings(api_key=settings.fmp_api_key, tickers=tickers),
    )
    return refresh


def _poll_sec_by_ticker(tickers: list[str]) -> dict[str, Any]:
    by_ticker: dict[str, dict[str, Any]] = {}
    totals = {"holdings": 0, "queries": 0, "articles_new": 0}
    for ticker in tickers:
        res = _capture(
            "sec",
            lambda ticker=ticker: poll_sec(
                user_agent=settings.sec_edgar_user_agent,
                filter_ticker=ticker,
                num_results=MORNING_SEC_RESULTS,
                lookback_hours=MORNING_NEWS_LOOKBACK_HOURS,
            ),
        )
        by_ticker[ticker] = res
        totals["holdings"] += int(res.get("holdings", 0) or 0)
        totals["queries"] += int(res.get("queries", 0) or 0)
        totals["articles_new"] += int(res.get("articles_new", 0) or 0)
    return {**totals, "by_ticker": by_ticker}


def _recompute_triggered_ticker(
    ticker: str,
    *,
    offline: bool,
    compute_source: str,
    fresh_article_ids: Sequence[str],
) -> dict[str, Any]:
    article_ids = _normalize_article_ids(fresh_article_ids)
    state: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "fresh_article_count": len(article_ids),
        "fresh_article_event_ids": article_ids,
    }
    if not article_ids:
        state["scoring"] = {"scored": 0, "errors": 0, "skipped": 0}
        state["red_team"] = {"ran": 0, "errors": 0, "skipped": 0}
        state["decision"] = {
            "decided": 0,
            "skipped": 1,
            "errors": 0,
            "holdings": 1,
            "reason": "no_fresh_evidence_article_ids",
        }
        state["finished_at"] = datetime.now(UTC).isoformat()
        return state
    state["scoring"] = score_unscored(
        api_key=settings.anthropic_api_key,
        offline=offline,
        ticker=ticker,
        article_event_ids=article_ids,
    )
    state["red_team"] = run_red_team(
        api_key=settings.anthropic_api_key,
        offline=offline,
        ticker=ticker,
        article_event_ids=article_ids,
    )
    state["decision"] = run_decisions(
        only_ticker=ticker,
        force=True,
        offline=offline,
        compute_source=compute_source,
        evidence_article_event_ids=article_ids,
    )
    state["finished_at"] = datetime.now(UTC).isoformat()
    return state


def _normalize_article_ids(article_event_ids: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(eid).strip() for eid in article_event_ids if str(eid).strip()))


def _new_counts(summary: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    raw = summary.get("by_ticker") if isinstance(summary, dict) else None
    if not isinstance(raw, dict):
        return out
    for ticker, value in raw.items():
        if isinstance(value, dict):
            out[ticker.upper()] = int(value.get("new", value.get("articles_new", 0)) or 0)
        else:
            out[ticker.upper()] = 0
    return out


def _open_pnl_pct(holding: Holding) -> float | None:
    if holding.cost_basis in (None, 0):
        return None
    return (holding.market_value - holding.cost_basis) / holding.cost_basis

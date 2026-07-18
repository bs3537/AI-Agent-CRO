"""Scheduled analyst-consensus target and FMP EOD upside workflows."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import settings
from ..news.fmp_client import (
    FmpError,
    fetch_is_etf,
    latest_is_etf_flags,
    latest_price_snapshot,
    refresh_prices_for_holdings,
)
from ..portfolio.joined import latest_joined
from .fmp import FMP_TARGET_DOCS_URL, FmpConsensusTarget, fetch_fmp_consensus
from .store import (
    FMP_SOURCE,
    FMP_WINDOW,
    apply_reference_price,
    mark_target_failure,
    save_target_not_applicable,
    save_target_success,
    save_target_unavailable,
)
from .tipranks import (
    TipRanksBrowserClient,
    TipRanksBrowserError,
    TipRanksTarget,
    ensure_tipranks_browser,
)

log = logging.getLogger(__name__)

# New York trading-date calculations stay DST-aware for the scheduled EOD job.
ET = ZoneInfo("America/New_York")


def refresh_fmp_targets(
    *,
    api_key: str | None,
    tickers: list[str] | None = None,
    fetch_fn: Callable[[str], FmpConsensusTarget | None] | None = None,
) -> dict[str, Any]:
    """Refresh FMP consensus targets for held equities."""
    if not api_key and fetch_fn is None:
        raise RuntimeError("FMP_API_KEY is required for analyst target refresh")
    all_tickers = _held_tickers(tickers)
    targets, skipped_etfs = _partition_etfs(
        all_tickers,
        api_key=api_key,
        target_source=FMP_SOURCE,
    )
    summary: dict[str, Any] = {
        "tickers": len(all_tickers),
        "eligible_equities": len(targets),
        "skipped_etfs": skipped_etfs,
        "updated": 0,
        "no_coverage": 0,
        "failed": 0,
        "stale_retained": 0,
        "source": FMP_SOURCE,
        "failures": [],
    }
    if not targets:
        return summary

    with httpx.Client(timeout=30.0) as client:
        for ticker in targets:
            attempted_at = datetime.now(UTC)
            try:
                target = (
                    fetch_fn(ticker)
                    if fetch_fn is not None
                    else fetch_fmp_consensus(ticker, api_key=api_key or "", client=client)
                )
                if target is None:
                    save_target_unavailable(
                        ticker,
                        attempted_at=attempted_at,
                        source=FMP_SOURCE,
                        target_window=FMP_WINDOW,
                        source_url=FMP_TARGET_DOCS_URL,
                    )
                    summary["no_coverage"] += 1
                else:
                    save_target_success(
                        ticker,
                        target,
                        fetched_at=attempted_at,
                        source=FMP_SOURCE,
                        target_window=FMP_WINDOW,
                        source_url=FMP_TARGET_DOCS_URL,
                    )
                    summary["updated"] += 1
                    _apply_latest_price(ticker, updated_at=attempted_at)
            except Exception as exc:  # noqa: BLE001 - one ticker must not abort the book.
                _record_failure(
                    summary,
                    ticker,
                    attempted_at,
                    exc,
                    source=FMP_SOURCE,
                    target_window=FMP_WINDOW,
                    source_url=FMP_TARGET_DOCS_URL,
                )
    return summary


# Refresh TipRanks targets for all dashboard positions or an explicit ticker subset.
def refresh_tipranks_targets(
    *,
    tickers: list[str] | None = None,
    start_browser: bool = True,
    delay_seconds: float | None = None,
    scrape_fn: Callable[[str], TipRanksTarget | None] | None = None,
) -> dict[str, Any]:
    all_tickers = _held_tickers(tickers)
    targets, skipped_etfs = _partition_etfs(
        all_tickers,
        api_key=settings.fmp_api_key,
        target_source="tipranks",
    )
    delay = (
        settings.tipranks_request_delay_seconds
        if delay_seconds is None
        else max(0.0, delay_seconds)
    )
    summary: dict[str, Any] = {
        "tickers": len(all_tickers),
        "eligible_equities": len(targets),
        "skipped_etfs": skipped_etfs,
        "updated": 0,
        "no_coverage": 0,
        "failed": 0,
        "stale_retained": 0,
        "source": "tipranks",
        "failures": [],
    }
    if not targets:
        return summary

    browser_context = (
        nullcontext(settings.tipranks_browser_url)
        if scrape_fn is not None
        else ensure_tipranks_browser(
            base_url=settings.tipranks_browser_url,
            command=settings.tipranks_browser_command,
            start_if_needed=start_browser,
            start_timeout_seconds=settings.tipranks_browser_start_timeout_seconds,
        )
    )
    try:
        with browser_context as browser_url:
            browser = None
            if scrape_fn is None:
                browser = TipRanksBrowserClient(
                    browser_url,
                    render_wait_seconds=settings.tipranks_render_wait_seconds,
                )
                scrape_fn = browser.fetch_target
            try:
                for index, ticker in enumerate(targets):
                    attempted_at = datetime.now(UTC)
                    try:
                        target = scrape_fn(ticker)
                        if target is None:
                            save_target_unavailable(ticker, attempted_at=attempted_at)
                            summary["no_coverage"] += 1
                        else:
                            save_target_success(ticker, target, fetched_at=attempted_at)
                            summary["updated"] += 1
                            _apply_latest_price(ticker, updated_at=attempted_at)
                    except Exception as exc:  # noqa: BLE001 - one ticker must not abort the book.
                        _record_failure(summary, ticker, attempted_at, exc)
                    if delay and index < len(targets) - 1:
                        time.sleep(delay)
            finally:
                if browser is not None:
                    browser.close()
    except TipRanksBrowserError as exc:
        for ticker in targets:
            _record_failure(summary, ticker, datetime.now(UTC), exc)
    return summary


# Refresh FMP consensus targets and EOD histories, then recalculate upside.
def refresh_eod_target_upside(
    *,
    api_key: str | None,
    tickers: list[str] | None = None,
    retry_attempts: int = 3,
    retry_seconds: float = 300.0,
    expected_price_date: str | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    all_tickers = _held_tickers(tickers)
    if not api_key:
        raise RuntimeError("FMP_API_KEY is required for the EOD target-upside refresh")
    target_refresh = refresh_fmp_targets(api_key=api_key, tickers=all_tickers)
    targets, skipped_etfs = _partition_etfs(
        all_tickers,
        api_key=api_key,
        target_source=FMP_SOURCE,
    )
    expected = expected_price_date or datetime.now(ET).date().isoformat()
    pending = list(targets)
    refreshes: list[dict[str, Any]] = []
    for attempt in range(max(1, retry_attempts)):
        if not pending:
            break
        refreshes.append(
            refresh_prices_for_holdings(api_key=api_key, tickers=pending)
        )
        pending = [
            ticker
            for ticker in pending
            if (latest_price_snapshot(ticker) or {}).get("end_date") != expected
        ]
        if pending and attempt < max(1, retry_attempts) - 1:
            sleep_fn(max(0.0, retry_seconds))

    updated = unchanged = no_target = no_price = 0
    for ticker in targets:
        snapshot = latest_price_snapshot(ticker)
        if not snapshot or not snapshot.get("closes") or not snapshot.get("end_date"):
            no_price += 1
            continue
        applied = _apply_latest_price(ticker, updated_at=datetime.now(UTC))
        if applied is None:
            no_target += 1
        elif applied:
            updated += 1
        else:
            unchanged += 1
    return {
        "tickers": len(all_tickers),
        "eligible_equities": len(targets),
        "skipped_etfs": skipped_etfs,
        "updated": updated,
        "unchanged": unchanged,
        "no_target": no_target,
        "no_price": no_price,
        "expected_price_date": expected,
        "pending_price_date": pending,
        "price_refreshes": refreshes,
        "target_refresh": target_refresh,
        "source": "fmp_consensus_and_eod_close",
    }


# Apply the latest dated EOD close to one target, returning None when no target exists.
def _apply_latest_price(ticker: str, *, updated_at: datetime) -> bool | None:
    from .store import latest_target_state

    state = latest_target_state(ticker)
    if not state or state.get("mean_price_target") is None:
        return None
    snapshot = latest_price_snapshot(ticker)
    closes = snapshot.get("closes") if snapshot else None
    price_as_of = snapshot.get("end_date") if snapshot else None
    if not closes or not price_as_of:
        return False
    return apply_reference_price(
        ticker,
        reference_close=float(closes[-1]),
        price_as_of=str(price_as_of),
        updated_at=updated_at,
    )


# Mark one failed attempt and count whether a prior successful target was retained.
def _record_failure(
    summary: dict[str, Any],
    ticker: str,
    attempted_at: datetime,
    exc: Exception,
    source: str = "tipranks",
    target_window: str = "12_month_targets_issued_last_3_months",
    source_url: str | None = None,
) -> None:
    from .store import latest_target_state

    mark_target_failure(
        ticker,
        attempted_at=attempted_at,
        source=source,
        target_window=target_window,
        source_url=source_url,
    )
    summary["failed"] += 1
    summary["failures"].append({"ticker": ticker, "reason": type(exc).__name__})
    state = latest_target_state(ticker)
    if state and state["status"] == "stale":
        summary["stale_retained"] += 1
    log.exception(
        "analyst_target_failed",
        extra={"ticker": ticker, "error_type": type(exc).__name__},
        exc_info=exc,
    )


# Exclude FMP-classified ETFs and mark their target state as not applicable.
def _partition_etfs(
    tickers: list[str],
    *,
    api_key: str | None,
    target_source: str,
) -> tuple[list[str], list[str]]:
    equities: list[str] = []
    etfs: list[str] = []
    cached_flags = latest_is_etf_flags(tickers)
    with httpx.Client(timeout=30.0) as client:
        for ticker in tickers:
            flag = cached_flags.get(ticker)
            if flag is None and api_key:
                try:
                    flag = fetch_is_etf(
                        ticker,
                        api_key=api_key,
                        client=client,
                    )
                except FmpError:
                    log.warning("etf_classification_failed", extra={"ticker": ticker})
            if flag is True:
                save_target_not_applicable(
                    ticker,
                    source=target_source,
                    target_window=(
                        FMP_WINDOW
                        if target_source == FMP_SOURCE
                        else "12_month_targets_issued_last_3_months"
                    ),
                    source_url=(
                        FMP_TARGET_DOCS_URL
                        if target_source == FMP_SOURCE
                        else None
                    ),
                )
                etfs.append(ticker)
            else:
                equities.append(ticker)
    return equities, etfs


# Normalize an optional explicit ticker list or load the current joined holdings.
def _held_tickers(tickers: list[str] | None) -> list[str]:
    if tickers is None:
        holdings, _missing, _pulled_at = latest_joined()
        tickers = [holding.ticker for holding in holdings]
    return sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})

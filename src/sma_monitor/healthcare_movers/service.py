"""Bootstrap and nightly refresh workflow for healthcare mover rankings."""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from ..news.fmp_client import FmpError
from .fmp import (
    NEW_YORK,
    fetch_batch_quotes,
    fetch_healthcare_universe,
    fetch_history_points,
)
from .ranking import compute_mover_rankings
from .store import (
    active_universe,
    price_histories,
    save_price_points,
    save_ranking_run,
    save_universe_snapshot,
)

log = logging.getLogger(__name__)

MIN_VALID_COVERAGE = 0.95
BACKFILL_CALENDAR_DAYS = 60
BACKFILL_WORKERS = 4

UniverseFetcher = Callable[..., list[dict[str, Any]]]
QuoteFetcher = Callable[..., list[dict[str, Any]]]
HistoryFetcher = Callable[..., list[dict[str, Any]]]


def refresh_healthcare_movers(
    *,
    api_key: str | None,
    bootstrap: bool = False,
    min_coverage: float = MIN_VALID_COVERAGE,
    max_workers: int = BACKFILL_WORKERS,
    now: datetime | None = None,
    universe_fetcher: UniverseFetcher = fetch_healthcare_universe,
    quote_fetcher: QuoteFetcher = fetch_batch_quotes,
    history_fetcher: HistoryFetcher = fetch_history_points,
) -> dict[str, Any]:
    """Refresh source data and publish a ranking only when coverage is sufficient."""
    if not api_key:
        raise RuntimeError("FMP_API_KEY is required for healthcare movers")
    now = now or datetime.now(UTC)
    universe_rows = universe_fetcher(api_key=api_key)
    universe_summary = save_universe_snapshot(universe_rows, observed_at=now)
    universe = active_universe()
    tickers = [row["ticker"] for row in universe]
    existing = price_histories(tickers, max_sessions=30)
    backfill_tickers = [
        ticker
        for ticker in tickers
        if bootstrap or len(existing.get(ticker, ())) < 6
    ]
    errors: list[str] = []
    history_points = _fetch_backfills(
        backfill_tickers,
        api_key=api_key,
        now=now,
        max_workers=max_workers,
        history_fetcher=history_fetcher,
        errors=errors,
    )
    history_saved = save_price_points(history_points, fetched_at=now)

    quote_points: list[dict[str, Any]] = []
    try:
        quote_points = quote_fetcher(tickers, api_key=api_key, now=now)
    except (FmpError, RuntimeError, ValueError) as exc:
        errors.append(f"quotes: {exc}")
        log.warning("healthcare_movers_quotes_failed", extra={"error": str(exc)})
    quotes_saved = save_price_points(quote_points, fetched_at=now)

    histories = price_histories(tickers, max_sessions=30)
    result = compute_mover_rankings(universe, histories)
    universe_count = len(tickers)
    history_covered = int(result["covered_by_window"]["5"])
    quote_tickers = {str(row.get("ticker") or "").upper() for row in quote_points}
    quote_coverage = len(quote_tickers & set(tickers)) / universe_count if universe_count else 0.0
    history_coverage = history_covered / universe_count if universe_count else 0.0
    coverage = min(history_coverage, quote_coverage)
    is_current = universe_count > 0 and coverage >= min_coverage
    status = "current" if is_current else "failed"
    if not is_current:
        errors.append(
            f"coverage below threshold: history={history_coverage:.1%}, "
            f"quotes={quote_coverage:.1%}, required={min_coverage:.1%}"
        )
    run_id = save_ranking_run(
        result,
        status=status,
        generated_at=now,
        error_summary="; ".join(errors)[:2000] or None,
    )
    return {
        "run_id": run_id,
        "status": status,
        "as_of_date": result["as_of_date"],
        "universe": universe_summary,
        "universe_count": universe_count,
        "backfill_requested": len(backfill_tickers),
        "history_points_saved": history_saved,
        "quotes_saved": quotes_saved,
        "history_coverage": history_coverage,
        "quote_coverage": quote_coverage,
        "errors": errors,
    }


def _fetch_backfills(
    tickers: Sequence[str],
    *,
    api_key: str,
    now: datetime,
    max_workers: int,
    history_fetcher: HistoryFetcher,
    errors: list[str],
) -> list[dict[str, Any]]:
    if not tickers:
        return []
    ny_date = now.astimezone(NEW_YORK).date()
    from_date = (ny_date - timedelta(days=BACKFILL_CALENDAR_DAYS)).isoformat()
    to_date = ny_date.isoformat()
    points: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(
                history_fetcher,
                ticker,
                api_key=api_key,
                from_date=from_date,
                to_date=to_date,
            ): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                points.extend(future.result())
            except (FmpError, RuntimeError, ValueError) as exc:
                errors.append(f"{ticker}: {exc}")
                log.warning(
                    "healthcare_movers_history_failed",
                    extra={"ticker": ticker, "error": str(exc)},
                )
    return points


def ranking_health(result: Mapping[str, Any]) -> str:
    """Human-readable coverage summary for CLI logs."""
    return (
        f"{result.get('status')} as of {result.get('as_of_date') or 'n/a'}; "
        f"{result.get('universe_count', 0)} symbols; "
        f"history {float(result.get('history_coverage') or 0):.1%}; "
        f"quotes {float(result.get('quote_coverage') or 0):.1%}"
    )

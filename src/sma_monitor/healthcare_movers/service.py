"""Bootstrap and nightly refresh workflow for healthcare mover rankings."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
PRIMARY_LISTING_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX"})

UniverseFetcher = Callable[..., list[dict[str, Any]]]
QuoteFetcher = Callable[..., list[dict[str, Any]]]
HistoryFetcher = Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True)
class QuoteAlignment:
    points: list[dict[str, Any]]
    session_date: str | None
    response_count: int
    fresh_count: int
    carried_count: int


def align_quotes_to_market_session(
    universe: Sequence[Mapping[str, Any]],
    quote_points: Sequence[Mapping[str, Any]],
) -> QuoteAlignment:
    """Carry non-trading symbols forward to the dominant primary-exchange session."""
    exchange_by_ticker = {
        str(row.get("ticker") or "").strip().upper(): str(row.get("exchange") or "").upper()
        for row in universe
        if row.get("ticker")
    }
    by_ticker = {
        str(row.get("ticker") or "").strip().upper(): dict(row)
        for row in quote_points
        if str(row.get("ticker") or "").strip().upper() in exchange_by_ticker
        and row.get("price_date")
    }
    primary_dates = [
        str(row["price_date"])
        for ticker, row in by_ticker.items()
        if exchange_by_ticker[ticker] in PRIMARY_LISTING_EXCHANGES
    ]
    candidate_dates = primary_dates or [str(row["price_date"]) for row in by_ticker.values()]
    if not candidate_dates:
        return QuoteAlignment([], None, 0, 0, 0)
    counts = Counter(candidate_dates)
    session_date = max(counts, key=lambda date: (counts[date], date))
    aligned: list[dict[str, Any]] = []
    fresh_count = 0
    for ticker, row in sorted(by_ticker.items()):
        is_fresh = str(row["price_date"]) == session_date
        fresh_count += int(is_fresh)
        aligned.append(
            {
                **row,
                "ticker": ticker,
                "price_date": session_date,
                "volume": row.get("volume") if is_fresh else 0,
            }
        )
    return QuoteAlignment(
        points=aligned,
        session_date=session_date,
        response_count=len(aligned),
        fresh_count=fresh_count,
        carried_count=len(aligned) - fresh_count,
    )


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

    raw_quote_points: list[dict[str, Any]] = []
    try:
        raw_quote_points = quote_fetcher(tickers, api_key=api_key, now=now)
    except (FmpError, RuntimeError, ValueError) as exc:
        errors.append(f"quotes: {exc}")
        log.warning("healthcare_movers_quotes_failed", extra={"error": str(exc)})
    alignment = align_quotes_to_market_session(universe, raw_quote_points)
    quote_points = alignment.points
    quotes_saved = save_price_points(quote_points, fetched_at=now)

    histories = price_histories(tickers, max_sessions=30)
    result = compute_mover_rankings(
        universe,
        histories,
        as_of_date=alignment.session_date,
    )
    universe_count = len(tickers)
    history_covered = int(result["covered_by_window"]["5"])
    quote_tickers = {str(row.get("ticker") or "").upper() for row in quote_points}
    quote_coverage = len(quote_tickers & set(tickers)) / universe_count if universe_count else 0.0
    fresh_quote_coverage = alignment.fresh_count / universe_count if universe_count else 0.0
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
        "market_session_date": alignment.session_date,
        "history_coverage": history_coverage,
        "quote_coverage": quote_coverage,
        "fresh_quote_coverage": fresh_quote_coverage,
        "carried_forward_quotes": alignment.carried_count,
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
        f"quotes {float(result.get('quote_coverage') or 0):.1%}; "
        f"fresh trades {float(result.get('fresh_quote_coverage') or 0):.1%}; "
        f"carried {int(result.get('carried_forward_quotes') or 0)}"
    )

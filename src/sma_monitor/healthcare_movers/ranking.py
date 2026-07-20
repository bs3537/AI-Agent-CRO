"""Pure ranking logic for one through five completed market sessions."""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

WINDOWS = (1, 2, 3, 4, 5)
DIRECTIONS = ("gainers", "decliners")
LOW_DOLLAR_VOLUME = 100_000.0
LOW_SHARE_VOLUME = 10_000
VOLUME_SPIKE_RATIO = 3.0
MIN_RANK_DOLLAR_VOLUME = LOW_DOLLAR_VOLUME


def compute_mover_rankings(
    universe: Sequence[Mapping[str, Any]],
    prices_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    as_of_date: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Return deterministic top gainers and decliners for 1-5 market sessions."""
    normalized_universe = {
        str(row["ticker"]).strip().upper(): dict(row)
        for row in universe
        if row.get("ticker")
    }
    histories = {
        ticker: _normalize_history(prices_by_ticker.get(ticker, ()))
        for ticker in normalized_universe
    }
    sessions = _market_sessions(
        histories.values(),
        universe_count=len(normalized_universe),
        as_of_date=as_of_date,
    )
    if not sessions:
        return _empty_result(len(normalized_universe))

    latest_session = sessions[-1]
    candidates: dict[int, list[dict[str, Any]]] = {window: [] for window in WINDOWS}
    covered: dict[str, int] = {str(window): 0 for window in WINDOWS}

    for ticker, metadata in normalized_universe.items():
        history = histories[ticker]
        if latest_session not in history:
            continue
        latest = history[latest_session]
        latest_close = latest["close"]
        prior_volumes = [
            history[date]["volume"]
            for date in sessions
            if date < latest_session
            and date in history
            and history[date]["volume"] is not None
        ][-20:]
        average_volume_20d = (
            sum(prior_volumes) / len(prior_volumes) if prior_volumes else None
        )
        volume_ratio = (
            latest["volume"] / average_volume_20d
            if latest["volume"] is not None
            and average_volume_20d
            and average_volume_20d > 0
            else None
        )
        flags = _flags(
            close=latest_close,
            volume=latest["volume"],
            volume_ratio=volume_ratio,
            observed_sessions=len(history),
        )
        spark_dates = sessions[-6:]
        spark_closes = [
            history[date]["close"] if date in history else None for date in spark_dates
        ]

        for window in WINDOWS:
            if len(sessions) <= window:
                continue
            start_date = sessions[-(window + 1)]
            if start_date not in history:
                continue
            start_close = history[start_date]["close"]
            if start_close <= 0:
                continue
            return_pct = (latest_close / start_close - 1.0) * 100.0
            covered[str(window)] += 1
            if not _is_rank_eligible(latest_close, latest["volume"]):
                continue
            candidates[window].append(
                {
                    "ticker": ticker,
                    "company_name": metadata.get("company_name") or ticker,
                    "industry": metadata.get("industry"),
                    "exchange": metadata.get("exchange"),
                    "market_cap": _optional_float(metadata.get("market_cap")),
                    "price": latest_close,
                    "return_pct": return_pct,
                    "window_days": window,
                    "start_date": start_date,
                    "end_date": latest_session,
                    "start_close": start_close,
                    "end_close": latest_close,
                    "latest_volume": latest["volume"],
                    "average_volume_20d": average_volume_20d,
                    "volume_ratio": volume_ratio,
                    "flags": flags,
                    "spark_dates": spark_dates,
                    "spark_closes": spark_closes,
                }
            )

    rankings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for window in WINDOWS:
        window_rows = candidates[window]
        gainers = [row for row in window_rows if row["return_pct"] > 0]
        decliners = [row for row in window_rows if row["return_pct"] < 0]
        gainers.sort(key=_gainer_key)
        decliners.sort(key=_decliner_key)
        rankings[str(window)] = {
            "gainers": _rank(gainers[:top_n]),
            "decliners": _rank(decliners[:top_n]),
        }

    return {
        "as_of_date": latest_session,
        "sessions": sessions,
        "universe_count": len(normalized_universe),
        "covered_by_window": covered,
        "rankings": rankings,
    }


def _normalize_history(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    history: dict[str, dict[str, Any]] = {}
    for row in rows:
        date = str(row.get("price_date") or row.get("date") or "").strip()
        close = _optional_float(row.get("close"))
        if not date or close is None or not math.isfinite(close) or close <= 0:
            continue
        volume = _optional_int(row.get("volume"))
        history[date] = {"close": close, "volume": volume}
    return history


def _market_sessions(
    histories: Iterable[Mapping[str, Mapping[str, Any]]],
    *,
    universe_count: int,
    as_of_date: str | None,
) -> list[str]:
    counts: Counter[str] = Counter()
    for history in histories:
        counts.update(history.keys())
    if not counts:
        return []
    threshold = max(1, math.ceil(universe_count * 0.5))
    sessions = sorted(date for date, count in counts.items() if count >= threshold)
    if not sessions:
        sessions = sorted(counts)
    if as_of_date:
        sessions = [date for date in sessions if date <= as_of_date]
    return sessions


def _flags(
    *,
    close: float,
    volume: int | None,
    volume_ratio: float | None,
    observed_sessions: int,
) -> list[str]:
    flags: list[str] = []
    if close < 1:
        flags.append("under_one_dollar")
    if volume is None or volume < LOW_SHARE_VOLUME or close * volume < LOW_DOLLAR_VOLUME:
        flags.append("low_liquidity")
    if observed_sessions < 6:
        flags.append("new_or_incomplete_history")
    if volume_ratio is not None and volume_ratio >= VOLUME_SPIKE_RATIO:
        flags.append("volume_spike")
    return flags


def _gainer_key(row: Mapping[str, Any]) -> tuple[float, float, str]:
    return (
        -float(row["return_pct"]),
        -_dollar_volume(row),
        str(row["ticker"]),
    )


def _decliner_key(row: Mapping[str, Any]) -> tuple[float, float, str]:
    return (
        float(row["return_pct"]),
        -_dollar_volume(row),
        str(row["ticker"]),
    )


def _dollar_volume(row: Mapping[str, Any]) -> float:
    volume = row.get("latest_volume")
    return float(row["price"]) * float(volume or 0)


def _is_rank_eligible(close: float, volume: int | None) -> bool:
    return volume is not None and close * volume >= MIN_RANK_DOLLAR_VOLUME


def _rank(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "rank": index} for index, row in enumerate(rows, start=1)]


def _empty_result(universe_count: int) -> dict[str, Any]:
    return {
        "as_of_date": None,
        "sessions": [],
        "universe_count": universe_count,
        "covered_by_window": {str(window): 0 for window in WINDOWS},
        "rankings": {
            str(window): {direction: [] for direction in DIRECTIONS}
            for window in WINDOWS
        },
    }


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None

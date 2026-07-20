"""FMP adapter and refresh-orchestration tests for healthcare movers."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sma_monitor.db import connection
from sma_monitor.healthcare_movers.fmp import (
    fetch_batch_quotes,
    fetch_healthcare_universe,
    fetch_history_points,
)
from sma_monitor.healthcare_movers.service import refresh_healthcare_movers
from sma_monitor.healthcare_movers.store import latest_ranking_snapshot


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def json(self):
        return self._body


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        return self.responses.pop(0)

    def close(self):
        pass


def test_fmp_universe_filters_funds_warrants_and_duplicates():
    valid = {
        "symbol": "JSPR",
        "companyName": "Jasper Therapeutics, Inc.",
        "sector": "Healthcare",
        "industry": "Biotechnology",
        "country": "US",
        "exchangeShortName": "NASDAQ",
        "marketCap": 14_383_524,
        "price": 0.885,
        "isActivelyTrading": True,
        "isEtf": False,
        "isFund": False,
    }
    warrant = {**valid, "symbol": "BADW", "companyName": "Bad Bio Warrant"}
    fund = {**valid, "symbol": "FUND", "companyName": "Healthcare Fund", "isFund": True}
    misclassified = {
        **valid,
        "symbol": "GOLD",
        "companyName": "Not Actually Healthcare",
        "industry": "Gold",
    }
    otc = {
        **valid,
        "symbol": "OTCM",
        "companyName": "OTC Medical, Inc.",
        "industry": "Medical Devices",
        "exchangeShortName": "OTC",
    }
    client = _Client(
        [
            _Response([valid, warrant, misclassified]),
            _Response([valid]),
            _Response([fund]),
            _Response([otc]),
        ]
    )

    rows = fetch_healthcare_universe(api_key="fake", client=client)

    assert [row["ticker"] for row in rows] == ["JSPR", "OTCM"]
    assert rows[0]["company_name"] == "Jasper Therapeutics, Inc."
    assert rows[0]["market_cap"] == 14_383_524
    assert rows[1]["industry"] == "Medical Devices"
    assert [call[1]["exchange"] for call in client.calls] == [
        "NASDAQ",
        "NYSE",
        "AMEX",
        "OTC",
    ]


def test_fmp_batch_quotes_chunks_and_maps_timestamp_to_new_york_date():
    timestamp = int(datetime(2026, 7, 18, 1, 0, tzinfo=UTC).timestamp())
    client = _Client(
        [
            _Response(
                [
                    {"symbol": "AAA", "price": 12.5, "volume": 100, "timestamp": timestamp},
                    {"symbol": "BBB", "price": 5.0, "volume": 200, "timestamp": timestamp},
                ]
            ),
            _Response([{"symbol": "CCC", "price": 8.0, "volume": 300}]),
        ]
    )

    rows = fetch_batch_quotes(
        ["AAA", "BBB", "CCC"],
        api_key="fake",
        client=client,
        chunk_size=2,
        now=datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
    )

    assert len(client.calls) == 2
    assert client.calls[0][1]["symbols"] == "AAA,BBB"
    assert rows[0]["price_date"] == "2026-07-17"
    assert rows[-1]["price_date"] == "2026-07-17"


def test_fmp_history_is_normalized_oldest_first():
    client = _Client(
        [
            _Response(
                [
                    {"date": "2026-07-17", "close": 0.885, "volume": 1_500_000},
                    {"date": "2026-07-16", "close": 0.774, "volume": 1_400_000},
                ]
            )
        ]
    )

    rows = fetch_history_points(
        "jspr",
        api_key="fake",
        from_date="2026-07-01",
        to_date="2026-07-17",
        client=client,
    )

    assert [row["price_date"] for row in rows] == ["2026-07-16", "2026-07-17"]
    assert all(row["ticker"] == "JSPR" for row in rows)


def _service_universe():
    return [
        {
            "ticker": ticker,
            "company_name": f"{ticker} Therapeutics",
            "sector": "Healthcare",
            "industry": "Biotechnology",
            "exchange": "NASDAQ",
            "country": "US",
            "market_cap": 100_000_000,
        }
        for ticker in ("UPCO", "DOWN")
    ]


def _history(ticker, *, api_key, from_date, to_date):
    del api_key, from_date, to_date
    values = [10, 11, 12, 13, 14, 15] if ticker == "UPCO" else [10, 9, 8, 7, 6, 5]
    dates = [
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    ]
    return [
        {"ticker": ticker, "price_date": date, "close": close, "volume": 100_000}
        for date, close in zip(dates, values, strict=True)
    ]


def _quotes(tickers, *, api_key, now):
    del api_key, now
    prices = {"UPCO": 15, "DOWN": 5}
    return [
        {
            "ticker": ticker,
            "price_date": "2026-07-17",
            "close": prices[ticker],
            "volume": 100_000,
        }
        for ticker in tickers
    ]


def test_refresh_publishes_a_complete_snapshot():
    result = refresh_healthcare_movers(
        api_key="fake",
        bootstrap=True,
        now=datetime(2050, 7, 18, 3, 0, tzinfo=UTC),
        universe_fetcher=lambda **kwargs: _service_universe(),
        history_fetcher=_history,
        quote_fetcher=_quotes,
    )

    assert result["status"] == "current"
    assert result["history_coverage"] == 1
    assert result["quote_coverage"] == 1
    with connection() as conn:
        run = conn.execute(
            "SELECT status FROM healthcare_mover_runs WHERE run_id = ?",
            (result["run_id"],),
        ).fetchone()
        leader = conn.execute(
            """SELECT company_name FROM healthcare_mover_rankings
               WHERE run_id = ? AND window_days = 5 AND direction = 'gainers'
               ORDER BY rank LIMIT 1""",
            (result["run_id"],),
        ).fetchone()
    assert run["status"] == "current"
    assert leader["company_name"] == "UPCO Therapeutics"


def test_incomplete_refresh_does_not_replace_last_valid_snapshot():
    current = latest_ranking_snapshot()
    assert current is not None

    result = refresh_healthcare_movers(
        api_key="fake",
        now=datetime(2050, 7, 19, 3, 0, tzinfo=UTC),
        universe_fetcher=lambda **kwargs: _service_universe(),
        history_fetcher=_history,
        quote_fetcher=lambda *args, **kwargs: _quotes(["UPCO"], **kwargs),
    )

    assert result["status"] == "failed"
    assert result["quote_coverage"] == pytest.approx(0.5)
    assert latest_ranking_snapshot()["run_id"] == current["run_id"]

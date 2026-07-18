"""Analyst target parsing, retention, source, and EOD upside tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sma_monitor.analyst_targets.fmp import (
    FmpConsensusTarget,
    fetch_fmp_consensus,
    parse_fmp_consensus,
)
from sma_monitor.analyst_targets.store import (
    apply_reference_price,
    latest_target_state,
    mark_target_failure,
    save_target_not_applicable,
    save_target_success,
    save_target_unavailable,
)
from sma_monitor.analyst_targets.tipranks import (
    TipRanksBrowserClient,
    TipRanksBrowserError,
    TipRanksParseError,
    TipRanksTarget,
    parse_tipranks_forecast,
)


class _FmpResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def json(self):
        return self._body


class _FmpClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    def get(self, url, *, params):
        self.request = {"url": url, "params": params}
        return self.response


# FMP stable consensus responses map into the generic dashboard target shape.
def test_parse_and_fetch_fmp_consensus():
    body = [{
        "symbol": "AQST",
        "targetHigh": 12,
        "targetLow": 6,
        "targetConsensus": 9,
        "targetMedian": 8.5,
        "analystCount": 6,
    }]
    parsed = parse_fmp_consensus("AQST", body)
    assert parsed is not None
    assert parsed.mean_price_target == pytest.approx(9)
    assert parsed.high_price_target == pytest.approx(12)
    assert parsed.low_price_target == pytest.approx(6)
    assert parsed.analyst_count == 6

    client = _FmpClient(_FmpResponse(body))
    fetched = fetch_fmp_consensus("aqst", api_key="fake", client=client)
    assert fetched == parsed
    assert client.request["params"] == {"symbol": "AQST", "apikey": "fake"}


# Empty and non-positive FMP consensus responses are valid no-coverage results.
@pytest.mark.parametrize("body", [[], {}, [{"targetConsensus": 0}]])
def test_parse_fmp_consensus_no_coverage(body):
    assert parse_fmp_consensus("NONE", body) is None


# The rendered forecast prose yields mean/high/low targets and analyst count.
def test_parse_tipranks_forecast_success():
    text = """
    Based on 7 Wall Street analysts offering 12 month price targets for
    Aquestive Therapeutics in the last 3 months. The average price target is
    $8.57 with a high forecast of $11.00 and a low forecast of $6.00.
    """

    target = parse_tipranks_forecast("AQST", text)

    assert target is not None
    assert target.mean_price_target == pytest.approx(8.57)
    assert target.high_price_target == pytest.approx(11.0)
    assert target.low_price_target == pytest.approx(6.0)
    assert target.analyst_count == 7
    assert target.currency == "USD"


# An explicit no-coverage page is a valid unavailable result, not a scrape error.
def test_parse_tipranks_forecast_no_coverage():
    assert parse_tipranks_forecast("ETFZ", "There are no analyst ratings for ETFZ.") is None


# TipRanks also renders uncovered forecasts as em-dash values or NaN analyst counts.
@pytest.mark.parametrize(
    "text",
    [
        (
            "ELOX Analyst Recommendation Trends. ELOX average Analyst price "
            "target in the past 3 months is ―."
        ),
        (
            "MESO Analyst Ratings Currently, no data available. "
            "Based on NaN analysts. MESO Stock 12 Month Forecast. "
            "Highest ― Average Price Target ― Lowest ―"
        ),
        "Page Not Found. We couldn't find the page you requested.",
    ],
)
def test_parse_tipranks_forecast_empty_forecast_variants(text):
    assert parse_tipranks_forecast("NONE", text) is None


# Implausible target ordering is rejected so a layout change cannot corrupt the UI.
def test_parse_tipranks_forecast_rejects_invalid_range():
    text = (
        "Based on 3 Wall Street analysts. The average price target is $10.00 "
        "with a high forecast of $8.00 and a low forecast of $5.00."
    )
    with pytest.raises(TipRanksParseError, match="high price target"):
        parse_tipranks_forecast("BAD", text)


# A failed weekly scrape retains the last successful target and marks it stale.
def test_target_failure_retains_last_success_and_upside():
    ticker = "ZZTGT"
    fetched_at = datetime.now(UTC) - timedelta(days=2)
    save_target_success(
        ticker,
        TipRanksTarget(
            mean_price_target=120.0,
            high_price_target=150.0,
            low_price_target=90.0,
            analyst_count=8,
            currency="USD",
            source_url="https://www.tipranks.com/stocks/zztgt/forecast",
        ),
        fetched_at=fetched_at,
    )
    assert apply_reference_price(
        ticker,
        reference_close=100.0,
        price_as_of="2026-07-15",
        updated_at=fetched_at,
    )

    mark_target_failure(ticker, attempted_at=datetime.now(UTC))
    state = latest_target_state(ticker)

    assert state is not None
    assert state["status"] == "stale"
    assert state["mean_price_target"] == pytest.approx(120.0)
    assert state["upside_pct"] == pytest.approx(0.2)
    assert state["price_as_of"] == "2026-07-15"


# A confirmed no-coverage result clears a previously saved target.
def test_target_no_coverage_clears_obsolete_value():
    ticker = "ZZNONE"
    save_target_success(
        ticker,
        TipRanksTarget(
            mean_price_target=42.0,
            high_price_target=50.0,
            low_price_target=35.0,
            analyst_count=4,
            currency="USD",
            source_url="https://www.tipranks.com/stocks/zznone/forecast",
        ),
    )

    save_target_unavailable(ticker)
    state = latest_target_state(ticker)

    assert state is not None
    assert state["status"] == "unavailable"
    assert state["mean_price_target"] is None
    assert state["upside_pct"] is None


# Marking an ETF as not applicable clears any old equity target and upside.
def test_target_not_applicable_clears_obsolete_value():
    ticker = "ZZETF"
    save_target_success(
        ticker,
        TipRanksTarget(
            mean_price_target=42.0,
            high_price_target=50.0,
            low_price_target=35.0,
            analyst_count=4,
            currency="USD",
            source_url="https://www.tipranks.com/stocks/zzetf/forecast",
        ),
    )
    assert apply_reference_price(
        ticker,
        reference_close=40.0,
        price_as_of="2026-07-15",
    )

    save_target_not_applicable(ticker)
    state = latest_target_state(ticker)

    assert state is not None
    assert state["status"] == "not_applicable"
    assert state["mean_price_target"] is None
    assert state["upside_pct"] is None


# The browser client retries only transport/server failures, not parser failures.
def test_tipranks_browser_client_retries_transient_browser_error(monkeypatch):
    client = TipRanksBrowserClient(max_attempts=2, retry_wait_seconds=0)
    attempts = 0

    def fetch_once(ticker):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TipRanksBrowserError("transient")
        return None

    monkeypatch.setattr(client, "_fetch_target_once", fetch_once)
    try:
        assert client.fetch_target("MOBI") is None
    finally:
        client.close()

    assert attempts == 2


# FMP-classified ETFs are removed before any TipRanks page is requested.
def test_refresh_tipranks_targets_skips_etfs(monkeypatch):
    from sma_monitor.analyst_targets import service
    from sma_monitor.config import settings

    scraped: list[str] = []
    monkeypatch.setattr(
        service,
        "latest_is_etf_flags",
        lambda tickers: {"ETF1": True, "EQTY": False},
    )
    monkeypatch.setattr(
        service,
        "save_target_not_applicable",
        lambda ticker, **kwargs: "event-id",
    )
    monkeypatch.setattr(settings, "fmp_api_key", None)

    summary = service.refresh_tipranks_targets(
        tickers=["ETF1", "EQTY"],
        delay_seconds=0,
        scrape_fn=lambda ticker: scraped.append(ticker) or TipRanksTarget(
            mean_price_target=10.0,
            high_price_target=12.0,
            low_price_target=8.0,
            analyst_count=3,
            currency="USD",
            source_url=f"https://www.tipranks.com/stocks/{ticker.lower()}/forecast",
        ),
    )

    assert scraped == ["EQTY"]
    assert summary["skipped_etfs"] == ["ETF1"]
    assert summary["eligible_equities"] == 1


# The scheduled FMP refresh skips ETFs and persists FMP attribution for equities.
def test_refresh_fmp_targets_skips_etfs_and_sets_source(monkeypatch):
    from sma_monitor.analyst_targets import service

    fetched: list[str] = []
    monkeypatch.setattr(
        service,
        "latest_is_etf_flags",
        lambda tickers: {"ETF1": True, "EQTY": False},
    )

    summary = service.refresh_fmp_targets(
        api_key="fake",
        tickers=["ETF1", "EQTY"],
        fetch_fn=lambda ticker: fetched.append(ticker) or FmpConsensusTarget(
            mean_price_target=25.0,
            high_price_target=30.0,
            low_price_target=20.0,
            analyst_count=5,
            currency="USD",
        ),
    )

    assert fetched == ["EQTY"]
    assert summary["source"] == "fmp"
    assert summary["updated"] == 1
    assert summary["skipped_etfs"] == ["ETF1"]
    equity = latest_target_state("EQTY")
    etf = latest_target_state("ETF1")
    assert equity["source"] == "fmp"
    assert equity["mean_price_target"] == pytest.approx(25.0)
    assert etf["source"] == "fmp"
    assert etf["status"] == "not_applicable"

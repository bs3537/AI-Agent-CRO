"""W2 tests — Brave / Scite / FMP source adapters (no live keys).

Covers the response parsers (fixture → ExaResult / metrics shapes), the
missing-key guards (offline → RuntimeError, never a crash), the FMP snapshot
store round-trip, and that build_candidate folds a cached FMP snapshot into
the decision candidate. Runs against the conftest sandbox DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sma_monitor.news import brave_client, fmp_client, scite_client

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "news_cache"


# Brave fixture parses into ExaResults with publisher URLs + ISO dates.
def test_brave_parse_fixture():
    results = brave_client.load_response_file(FIXTURES / "_sample_brave_response.json")
    assert len(results) == 2
    r = results[0]
    assert r.title and r.url.startswith("https://") and r.excerpt
    assert r.published_at is not None and r.published_at.year == 2026


# Brave freshness maps a start/end window to the YYYY-MM-DDtoYYYY-MM-DD form.
def test_brave_freshness_range():
    from datetime import datetime, timezone
    f = brave_client._freshness(
        datetime(2026, 5, 20, tzinfo=timezone.utc), datetime(2026, 5, 27, tzinfo=timezone.utc)
    )
    assert f == "2026-05-20to2026-05-27"
    assert brave_client._freshness(None, None) is None


# Scite fixture parses into ExaResults with canonical doi.org URLs (→ tier 3).
def test_scite_parse_fixture():
    from sma_monitor.news.source_tiers import source_tier
    results = scite_client.load_response_file(FIXTURES / "_sample_scite_response.json")
    assert len(results) == 2
    r = results[0]
    assert r.url.startswith("https://doi.org/")
    assert source_tier(r.url) == 3  # peer-reviewed


# FMP parse_metrics flattens the endpoint sections into friendly keys.
def test_fmp_parse_metrics():
    body = {
        "profile": [{"companyName": "Vertex", "mktCap": 1.2e11, "price": 465.0}],
        "ratios": [{"currentRatioTTM": 2.7, "grossProfitMarginTTM": 0.88}],
        "key_metrics": [{"cashPerShareTTM": 45.0}],
    }
    m = fmp_client.parse_metrics(body)
    assert m["company"] == "Vertex" and m["current_ratio"] == 2.7 and m["cash_per_share"] == 45.0


# Every adapter refuses to run live without a key + without a fixture, raising
# a clear RuntimeError rather than crashing or making a keyless request.
def test_missing_key_guards():
    from sma_monitor.news.pipeline import _make_provider, _make_scite_provider
    from sma_monitor import config

    for attr in ("brave_search_api_key", "exa_api_key", "scite_api_key", "fmp_api_key"):
        setattr(config.settings, attr, None)
    with pytest.raises(RuntimeError):
        _make_provider(None, None)
    with pytest.raises(RuntimeError):
        _make_scite_provider(None, None)
    with pytest.raises(RuntimeError):
        fmp_client.refresh_for_holdings(api_key=None)


# FMP snapshot store round-trips and latest_fmp_metrics returns the newest.
def test_fmp_snapshot_roundtrip():
    metrics = {"company": "TestCo", "current_ratio": 1.9, "net_margin": 0.1}
    fmp_client.save_fmp_snapshot("ZZTEST", metrics)
    got = fmp_client.latest_fmp_metrics("zztest")  # case-insensitive
    assert got == metrics
    assert fmp_client.latest_fmp_metrics("NOPE_TICKER") is None


# refresh_for_holdings replays a {ticker: metrics} fixture into the store.
def test_fmp_refresh_from_fixture():
    res = fmp_client.refresh_for_holdings(
        api_key=None,
        from_file=FIXTURES / "_sample_fmp_metrics.json",
        tickers=["VRTX", "MRNA"],
    )
    assert res["updated"] == 2 and res["source"] == "fixture"
    assert fmp_client.latest_fmp_metrics("VRTX")["company"].startswith("Vertex")


# FMP historical-price body parses oldest→newest (FMP returns newest-first).
def test_price_history_parse():
    body = {"symbol": "X", "historical": [
        {"date": "2026-05-20", "close": 3.0},
        {"date": "2026-05-19", "close": 2.0},
        {"date": "2026-05-18", "close": 1.0},
    ]}
    assert fmp_client._parse_history(body) == [1.0, 2.0, 3.0]


# Price-series store round-trips; latest_price_series returns the newest array.
def test_price_series_roundtrip():
    fmp_client.save_price_series("ZZSPARK", [10.0, 11.0, 9.5])
    assert fmp_client.latest_price_series("zzspark") == [10.0, 11.0, 9.5]
    assert fmp_client.latest_price_series("NOPE") is None


# refresh_prices_for_holdings replays a {ticker: [closes]} fixture into the store.
def test_price_refresh_from_fixture():
    res = fmp_client.refresh_prices_for_holdings(
        api_key=None,
        from_file=FIXTURES / "_sample_fmp_prices.json",
        tickers=["VRTX", "MRNA"],
    )
    assert res["updated"] == 2 and res["source"] == "fixture"
    series = fmp_client.latest_price_series("VRTX")
    assert series and len(series) == 252 and all(isinstance(x, float) for x in series)


# A cached FMP snapshot flows into build_candidate.fmp_metrics for the decision.
def test_build_candidate_picks_up_fmp():
    from sma_monitor.decision.engine import build_candidate
    from sma_monitor.portfolio.joined import latest_joined

    holdings, _missing, _ = latest_joined()
    if not holdings:
        pytest.skip("no holdings in sandbox")
    h = holdings[0]
    fmp_client.save_fmp_snapshot(h.ticker, {"company": "X", "current_ratio": 2.0})
    cand = build_candidate(h)
    assert cand.fmp_metrics is not None and cand.fmp_metrics["current_ratio"] == 2.0

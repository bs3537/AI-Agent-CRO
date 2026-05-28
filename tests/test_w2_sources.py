"""W2 tests — Brave / Scite / FMP source adapters (no live keys).

Covers the response parsers (fixture → ExaResult / metrics shapes), the
missing-key guards (offline → RuntimeError, never a crash), the FMP snapshot
store round-trip, and that build_candidate folds a cached FMP snapshot into
the decision candidate. Runs against the conftest sandbox DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sma_monitor.news import (
    brave_client,
    clinicaltrials_client,
    fmp_client,
    pubmed_client,
    scite_client,
    sec_client,
    semantic_scholar_client,
)

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


# Semantic Scholar fixture parses into ExaResults with canonical doi.org URLs
# (→ tier 3) — the live literature source for bucket #10 (replaces Scite).
def test_semantic_scholar_parse_fixture():
    from sma_monitor.news.source_tiers import source_tier
    results = semantic_scholar_client.load_response_file(
        FIXTURES / "_sample_semantic_scholar_response.json"
    )
    assert len(results) == 2
    r = results[0]
    assert r.url.startswith("https://doi.org/")
    assert r.title and r.excerpt
    assert source_tier(r.url) == 3  # peer-reviewed


# SEC filings fixture parses into ExaResults with sec.gov Archives URLs (tier 1
# — regulatory/primary), the financials-primary source ahead of FMP.
def test_sec_parse_fixture():
    from sma_monitor.news.source_tiers import source_tier
    results = sec_client.load_response_file(FIXTURES / "_sample_sec_response.json")
    assert len(results) == 3
    r = results[0]
    assert r.url.startswith("https://www.sec.gov/Archives/edgar/data/")
    assert "10-Q" in r.title
    assert source_tier(r.url) == 1  # regulatory/primary


# PubMed esummary fixture parses into ExaResults with pubmed.ncbi.nlm.nih.gov
# URLs (tier 3) — the biomed-literature primary, ahead of Semantic Scholar.
def test_pubmed_parse_fixture():
    from sma_monitor.news.source_tiers import source_tier
    results = pubmed_client.load_response_file(FIXTURES / "_sample_pubmed_response.json")
    assert len(results) == 2
    r = results[0]
    assert r.url == "https://pubmed.ncbi.nlm.nih.gov/38661449/"
    assert "Exagamglogene" in r.title
    assert source_tier(r.url) == 3


# ClinicalTrials.gov v2 fixture parses into ExaResults with clinicaltrials.gov
# study URLs (tier 2) — biomed-literature primary alongside PubMed.
def test_ctgov_parse_fixture():
    from sma_monitor.news.source_tiers import source_tier
    results = clinicaltrials_client.load_response_file(FIXTURES / "_sample_ctgov_response.json")
    assert len(results) == 2
    r = results[0]
    assert r.url == "https://clinicaltrials.gov/study/NCT03745287"
    assert "exa-cel" in r.title.lower()
    assert source_tier(r.url) == 2


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
    from sma_monitor.news.pipeline import _make_provider, _make_literature_provider
    from sma_monitor import config

    for attr in ("brave_search_api_key", "exa_api_key", "scite_api_key",
                 "semantic_scholar_api_key", "fmp_api_key"):
        setattr(config.settings, attr, None)
    with pytest.raises(RuntimeError):
        _make_provider(None, None)
    with pytest.raises(RuntimeError):
        _make_literature_provider(None, None)
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


# poll_sec replays an EDGAR fixture into bucket #7 as tier-1 articles (the
# financials-primary source), tagged to the queried holding.
def test_poll_sec_stores_filings_offline():
    from sma_monitor.news.pipeline import poll_sec
    from sma_monitor.news.store import recent_articles
    res = poll_sec(user_agent="test-agent", filter_ticker="VRTX",
                   from_file=FIXTURES / "_sample_sec_response.json", num_results=3)
    assert res["holdings"] == 1 and res["queries"] == 1
    rows = recent_articles(ticker="VRTX", bucket_id=7, limit=10)
    assert any(r["source_tier"] == 1 for r in rows)

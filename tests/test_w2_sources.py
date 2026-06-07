"""W2 tests — Brave / Scite / FMP source adapters (no live keys).

Covers the response parsers (fixture → ExaResult / metrics shapes), the
missing-key guards (offline → RuntimeError, never a crash), the FMP snapshot
store round-trip, and that build_candidate folds a cached FMP snapshot into
the decision candidate. Runs against the conftest sandbox DB.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sma_monitor.news import (
    brave_client,
    clinicaltrials_client,
    fmp_client,
    ir_client,
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


def test_ir_client_parses_rss_and_html_links():
    class _Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

    class _Client:
        def __init__(self, pages):
            self.pages = pages

        def get(self, url, headers):
            return _Resp(self.pages[url])

    rss_url = "https://ir.example.com/rss.xml"
    page_url = "https://ir.example.com/news"
    client = _Client({
        rss_url: """<?xml version="1.0"?>
          <rss><channel><item>
            <title>Example announces Phase 3 data</title>
            <link>https://ir.example.com/news/2026-05-30-phase-3-data</link>
            <pubDate>Sat, 30 May 2026 12:00:00 GMT</pubDate>
            <description>Official company release.</description>
          </item></channel></rss>""",
        page_url: """<html><body>
          <a href="/news/2026/05/30/corporate-update">Corporate update press release</a>
          <a href="/careers">Careers</a>
        </body></html>""",
    })

    feed = ir_client._fetch_feed(
        rss_url,
        client=client,
        user_agent="test-agent",
        num_results=5,
    )
    html = ir_client._fetch_html_links(
        page_url,
        client=client,
        user_agent="test-agent",
        num_results=5,
    )

    assert feed[0].title == "Example announces Phase 3 data"
    assert feed[0].published_at is not None
    assert html[0].url == "https://ir.example.com/news/2026/05/30/corporate-update"
    assert len(html) == 1


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
        "profile": [{"companyName": "Vertex", "marketCap": 1.2e11, "price": 465.0}],
        "ratios": [{"currentRatioTTM": 2.7, "grossProfitMarginTTM": 0.88, "cashPerShareTTM": 45.0}],
        "key_metrics": [{"enterpriseValueTTM": 5.0e10}],
    }
    m = fmp_client.parse_metrics(body)
    assert m["company"] == "Vertex" and m["current_ratio"] == 2.7 and m["cash_per_share"] == 45.0
    assert m["market_cap"] == 1.2e11 and m["enterprise_value"] == 5.0e10


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


# FMP quote fetch falls back to per-symbol requests when batch returns no rows.
def test_fetch_quotes_falls_back_to_single_symbol_calls():
    calls: list[str] = []

    def get(_url, *, params):
        symbol = params["symbol"]
        calls.append(symbol)
        payloads = {
            "VRTX,MRNA": [],
            "VRTX": [{"symbol": "VRTX", "price": 465.25, "changesPercentage": 1.2}],
            "MRNA": [{"symbol": "MRNA", "price": 28.5, "changesPercentage": -0.8}],
        }
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: payloads[symbol],
        )

    quotes = fmp_client.fetch_quotes(
        ["VRTX", "MRNA"],
        api_key="fake",
        client=SimpleNamespace(get=get),
    )

    assert calls == ["VRTX,MRNA", "VRTX", "MRNA"]
    assert quotes == {
        "VRTX": {"price": 465.25, "change_pct": 1.2},
        "MRNA": {"price": 28.5, "change_pct": -0.8},
    }


# FMP /stable price history is a flat newest-first array; parse oldest→newest.
# (Still tolerates the legacy {historical:[...]} shape.)
def test_price_history_parse():
    body = [
        {"date": "2026-05-20", "close": 3.0},
        {"date": "2026-05-19", "close": 2.0},
        {"date": "2026-05-18", "close": 1.0},
    ]
    assert fmp_client._parse_history(body) == [1.0, 2.0, 3.0]
    legacy = {"symbol": "X", "historical": body}
    assert fmp_client._parse_history(legacy) == [1.0, 2.0, 3.0]


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


# Hardening: a total live wipeout (every request errors, e.g. a retired endpoint
# or revoked key) must RAISE — not silently cache nothing — so the collect cycle
# trips fmp_failure instead of reporting a healthy 0-update run.
def test_fmp_total_failure_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise fmp_client.FmpError("403 Legacy Endpoint")

    monkeypatch.setattr(fmp_client, "fetch_metrics", boom)
    with pytest.raises(fmp_client.FmpError):
        fmp_client.refresh_for_holdings(api_key="fake", tickers=["VRTX", "MRNA"])

    monkeypatch.setattr(fmp_client, "fetch_price_history", boom)
    with pytest.raises(fmp_client.FmpError):
        fmp_client.refresh_prices_for_holdings(api_key="fake", tickers=["VRTX", "MRNA"])


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


def test_poll_company_news_uses_exact_ir_source(monkeypatch):
    from datetime import UTC, datetime
    from sma_monitor.news.exa_client import ExaResult
    from sma_monitor.news.pipeline import poll_company_news
    from sma_monitor.news.store import recent_articles
    from sma_monitor.portfolio.schema import Holding

    holding = Holding(
        ticker="IRTX",
        qty=1.0,
        market_value=100.0,
        pct_nav=0.01,
        cost_basis=100.0,
        pulled_at=datetime(2026, 5, 30, tzinfo=UTC),
        nav=10_000.0,
        conviction_tier=3,
        stage="clinical_stage",
        thesis="IR exact source test",
        company_name="IR Test Therapeutics",
        press_release_rss_url="https://ir.example.com/rss.xml",
        catalysts=[],
    )
    monkeypatch.setattr(
        "sma_monitor.news.pipeline.latest_joined",
        lambda: ([holding], [], holding.pulled_at),
    )
    monkeypatch.setattr(
        ir_client,
        "search",
        lambda h, num_results: [
            ExaResult(
                title="IR Test announces FDA update",
                url="https://ir.example.com/news/2026-05-30-fda-update",
                published_at=datetime(2026, 5, 30, tzinfo=UTC),
                excerpt="Official company release.",
                score=None,
                raw={},
            )
        ],
    )

    res = poll_company_news(api_key=None, num_results=2)

    assert res["by_ticker"]["IRTX"]["new"] == 1
    assert res["by_ticker"]["IRTX"]["exact_ir_configured"] == 1
    rows = recent_articles(ticker="IRTX", bucket_id=9, limit=5)
    assert rows and rows[0]["source_tier"] == 1

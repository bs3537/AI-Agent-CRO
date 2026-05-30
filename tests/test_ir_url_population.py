from __future__ import annotations

from datetime import UTC, datetime

from sma_monitor.news.brave_web_client import BraveWebResult
from sma_monitor.news.ir_discovery import IrDiscoveryResult, _candidate
from sma_monitor.portfolio.ir_urls import populate_ir_urls_for_tickers
from sma_monitor.portfolio.schema import Position
from sma_monitor.portfolio.sidecar import load_sidecar


def test_populate_ir_urls_creates_missing_sidecar(monkeypatch):
    def fake_discover_ir_urls(*, ticker, company_name=None, api_key=None, max_results=10):
        return IrDiscoveryResult(
            ticker=ticker,
            ir_url=f"https://ir.{ticker.lower()}.example/",
            press_releases_url=f"https://ir.{ticker.lower()}.example/news-releases",
            press_release_rss_url=f"https://ir.{ticker.lower()}.example/rss",
            source_url=f"https://ir.{ticker.lower()}.example/news-releases",
            status="ok",
        )

    monkeypatch.setattr(
        "sma_monitor.portfolio.ir_urls.discover_ir_urls",
        fake_discover_ir_urls,
    )

    state = populate_ir_urls_for_tickers(["newu"], create_missing=True)

    assert state["updated"] == 1
    sc = load_sidecar("NEWU")
    assert sc is not None
    assert sc.ir_url == "https://ir.newu.example/"
    assert sc.press_releases_url == "https://ir.newu.example/news-releases"
    assert sc.press_release_rss_url == "https://ir.newu.example/rss"


def test_populate_ir_urls_skips_already_populated(monkeypatch):
    calls = 0

    def fake_discover_ir_urls(**kwargs):
        nonlocal calls
        calls += 1
        ticker = kwargs["ticker"]
        return IrDiscoveryResult(
            ticker=ticker,
            ir_url=f"https://ir.{ticker.lower()}.example/",
            press_releases_url=f"https://ir.{ticker.lower()}.example/news",
            status="ok",
        )

    monkeypatch.setattr(
        "sma_monitor.portfolio.ir_urls.discover_ir_urls",
        fake_discover_ir_urls,
    )
    populate_ir_urls_for_tickers(["SKIP"], create_missing=True, force=True)
    state = populate_ir_urls_for_tickers(["SKIP"], create_missing=True)

    assert state["skipped"] == 1
    assert state["results"]["SKIP"]["reason"] == "already_populated"
    assert calls == 1


def test_position_refresh_populates_ir_urls(monkeypatch):
    from sma_monitor.orchestrator.pipeline import _populate_ir_urls_after_position_refresh

    captured = {}

    def fake_populate(tickers, *, create_missing=False, force=False):
        captured["tickers"] = list(tickers)
        captured["create_missing"] = create_missing
        return {"updated": len(tickers)}

    monkeypatch.setattr(
        "sma_monitor.portfolio.ir_urls.populate_ir_urls_for_tickers",
        fake_populate,
    )
    positions = [
        Position(
            ticker="IBKRNEW",
            qty=10,
            market_value=1000,
            pct_nav=0.01,
            cost_basis=900,
            pulled_at=datetime(2026, 5, 30, tzinfo=UTC),
            nav=100_000,
        )
    ]

    state = _populate_ir_urls_after_position_refresh(positions)

    assert state == {"updated": 1}
    assert captured == {"tickers": ["IBKRNEW"], "create_missing": True}


def test_ir_candidate_rejects_aggregators_and_pdfs():
    aggregator = BraveWebResult(
        title="Press release",
        url="https://www.theglobeandmail.com/investing/markets/stocks/NBIS/pressreleases/x",
        description="Nebius press release copy",
        raw={},
    )
    pdf = BraveWebResult(
        title="X-FAB press release PDF",
        url="https://www.xfab.com/fileadmin/X-FAB/Investor_Relations/release.pdf",
        description="Investor Relations",
        raw={},
    )

    assert _candidate(aggregator, ticker="NBIS", company_name="Nebius Group N.V.").score == 0
    assert _candidate(pdf, ticker="XFAB", company_name=None).score == 0


def test_ir_candidate_allows_exact_ticker_host_without_company_name():
    row = BraveWebResult(
        title="Investor Relations: X-FAB",
        url="https://www.xfab.com/investors/",
        description="X-FAB Silicon Foundries investor news",
        raw={},
    )

    assert _candidate(row, ticker="XFAB", company_name=None).score > 0

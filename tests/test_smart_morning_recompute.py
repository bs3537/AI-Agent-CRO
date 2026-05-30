"""Morning smart recompute triage tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sma_monitor.orchestrator import smart_recompute
from sma_monitor.portfolio.schema import Holding


def _holding(
    ticker: str,
    *,
    market_value: float = 110.0,
    cost_basis: float | None = 100.0,
    pct_nav: float = 0.02,
) -> Holding:
    return Holding(
        ticker=ticker,
        qty=1.0,
        market_value=market_value,
        pct_nav=pct_nav,
        cost_basis=cost_basis,
        pulled_at=datetime(2026, 5, 30, tzinfo=UTC),
        nav=1_000.0,
        conviction_tier=3,
        stage="clinical_stage",
        thesis=f"{ticker} thesis",
        company_name=f"{ticker} Therapeutics",
        catalysts=[],
    )


def test_triage_triggers_on_fresh_news_sec_loss_and_below_ema():
    fresh_news = smart_recompute.triage_holding(
        _holding("NEWS"),
        fresh_news_count=1,
        fresh_sec_count=0,
        price_series=[100.0] * 25 + [110.0],
    )
    assert fresh_news["recompute"] is True
    assert "fresh_company_or_yahoo_news" in fresh_news["reasons"]

    fresh_sec = smart_recompute.triage_holding(
        _holding("SEC"),
        fresh_news_count=0,
        fresh_sec_count=1,
        price_series=[100.0] * 25 + [110.0],
    )
    assert fresh_sec["recompute"] is True
    assert "fresh_sec_filing" in fresh_sec["reasons"]

    loss = smart_recompute.triage_holding(
        _holding("LOSS", market_value=90.0, cost_basis=100.0),
        fresh_news_count=0,
        fresh_sec_count=0,
        price_series=[100.0] * 25 + [110.0],
    )
    assert loss["recompute"] is True
    assert "open_loss_ge_10pct" in loss["reasons"]
    assert loss["open_pnl_pct"] == pytest.approx(-0.10)

    below = smart_recompute.triage_holding(
        _holding("BELOW"),
        fresh_news_count=0,
        fresh_sec_count=0,
        price_series=[100.0] * 25 + [90.0],
    )
    assert below["recompute"] is True
    assert below["technical_state"] in {"below_ema20", "extended_below_ema20"}


def test_triage_preserves_quiet_prior_rating():
    quiet = smart_recompute.triage_holding(
        _holding("QUIET"),
        fresh_news_count=0,
        fresh_sec_count=0,
        price_series=[100.0] * 25 + [110.0],
    )
    assert quiet["recompute"] is False
    assert quiet["reasons"] == []


def test_smart_morning_recompute_only_runs_triggered(monkeypatch):
    holdings = [
        _holding("LOSS", market_value=90.0, cost_basis=100.0),
        _holding("QUIET"),
    ]

    monkeypatch.setattr(
        smart_recompute,
        "latest_joined",
        lambda: (holdings, [], datetime(2026, 5, 30, tzinfo=UTC)),
    )
    monkeypatch.setattr(
        smart_recompute,
        "_refresh_all_daily_signals",
        lambda tickers: {
            "company_news": {"by_ticker": {ticker: {"new": 0} for ticker in tickers}},
            "sec": {"by_ticker": {ticker: {"articles_new": 0} for ticker in tickers}},
            "financials": {"updated": len(tickers)},
            "prices": {"updated": len(tickers)},
        },
    )
    monkeypatch.setattr(
        smart_recompute,
        "latest_price_series",
        lambda ticker: [100.0] * 25 + ([90.0] if ticker == "LOSS" else [110.0]),
    )

    calls: list[str] = []

    def fake_recompute(ticker, *, offline, compute_source):
        calls.append(ticker)
        return {"decision": {"decided": 1, "skipped": 0, "errors": 0}}

    monkeypatch.setattr(smart_recompute, "_recompute_triggered_ticker", fake_recompute)

    res = smart_recompute.run_smart_morning_recompute(
        refresh_positions_fn=lambda force: {"refreshed": force},
    )

    assert calls == ["LOSS"]
    assert res["recomputed_tickers"] == ["LOSS"]
    assert [r["ticker"] for r in res["quiet"]] == ["QUIET"]
    assert res["decisions"]["decided"] == 1

"""Sourced, multi-sector upcoming-catalyst research tests."""
from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from sma_monitor.api.app import create_app
from sma_monitor.news.catalyst_outlook import (
    CATALYST_TIMEOUT_S,
    build_catalyst_prompt,
    latest_catalyst_outlook,
    refresh_catalyst_outlooks_for_holdings,
    save_catalyst_outlook,
)
from sma_monitor.portfolio.schema import Holding

HELD = "VRTX"


def _holding(ticker: str = "TECH") -> Holding:
    return Holding(
        ticker=ticker,
        qty=1.0,
        market_value=100.0,
        pct_nav=0.01,
        cost_basis=90.0,
        pulled_at=datetime(2035, 1, 1, tzinfo=UTC),
        nav=10_000.0,
        conviction_tier=3,
        stage="commercial_stage",
        thesis="Cloud software platform with recurring revenue.",
        company_name="Technology Test Company",
        products=["Platform One"],
        catalysts=[],
    )


def _item(
    *,
    event_date: str | None,
    label: str,
    event_type: str = "product_launch",
) -> dict:
    return {
        "date": event_date,
        "date_label": event_date or "H2 2027",
        "type": event_type,
        "label": label,
        "confirmed": event_date is not None,
        "source_title": "Company investor relations",
        "source_url": "https://example.com/investors/event",
    }


def test_store_orders_clamps_and_preserves_sources():
    save_catalyst_outlook(
        "ZZCAT",
        [
            _item(event_date=None, label="Guided platform launch"),
            _item(event_date="2027-09-01", label="September investor day"),
            _item(event_date="2027-03-01", label="March product release"),
            _item(event_date="2027-06-01", label="June earnings"),
        ],
    )

    got = latest_catalyst_outlook("ZZCAT")
    assert [item["date"] for item in got] == [
        "2027-03-01",
        "2027-06-01",
        "2027-09-01",
    ]
    assert got[0]["source_title"] == "Company investor relations"
    assert got[0]["source_url"].startswith("https://")


def test_operating_company_prompt_is_multi_sector_and_source_required():
    system, user = build_catalyst_prompt(
        _holding(),
        is_etf=False,
        today=date(2026, 7, 18),
    )

    assert "multi-sector" in system
    assert "product launches" in user
    assert "earnings" in user
    assert "Technology Test Company" in user
    assert "2028-01-18" in user
    assert "direct source_url" in user
    assert "healthcare-only" not in (system + user).lower()


def test_etf_prompt_uses_fund_events_and_excludes_company_targets():
    _system, user = build_catalyst_prompt(
        _holding("QQQ"),
        is_etf=True,
        today=date(2026, 7, 18),
    )

    assert "index reconstitutions" in user
    assert "scheduled distributions" in user
    assert "Do not report company earnings or analyst price targets" in user


def test_fixture_refresh_round_trip(tmp_path):
    fixture = {
        HELD: [
            _item(
                event_date="2027-01-31",
                label="Scheduled earnings release",
                event_type="earnings",
            )
        ]
    }
    path = tmp_path / "catalysts.json"
    path.write_text(json.dumps(fixture))

    result = refresh_catalyst_outlooks_for_holdings(
        tickers=[HELD],
        from_file=path,
    )

    assert result["source"] == "fixture"
    assert result["updated"] == 1
    assert latest_catalyst_outlook(HELD)[0]["type"] == "earnings"


def test_live_refresh_passes_timeout_and_retains_last_success_on_failure():
    existing = _item(event_date="2027-04-01", label="Existing sourced event")
    save_catalyst_outlook(HELD, [existing])

    class FailingProvider:
        model_label = "fake-codex"

        def complete_json(self, **kwargs):
            assert kwargs["timeout_s"] == CATALYST_TIMEOUT_S
            raise RuntimeError("research failed")

    result = refresh_catalyst_outlooks_for_holdings(
        tickers=[HELD],
        provider=FailingProvider(),
        workers=1,
    )

    assert result["errors"] == 1
    assert result["updated"] == 0
    assert latest_catalyst_outlook(HELD)[0]["label"] == existing["label"]


def test_api_serves_catalyst_outlook():
    save_catalyst_outlook(
        HELD,
        [_item(event_date="2027-05-01", label="Regulatory submission", event_type="regulatory")],
    )

    with TestClient(create_app()) as client:
        positions = client.get("/api/positions").json()["positions"]

    row = next(position for position in positions if position["ticker"] == HELD)
    assert row["catalyst_outlook"][0]["type"] == "regulatory"
    assert row["catalyst_outlook"][0]["source_url"].startswith("https://")


def test_codex_timeout_and_stage_web_search_flags(monkeypatch):
    from sma_monitor.llm import codex_client

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(codex_client.subprocess, "run", fake_run)
    provider = codex_client.CodexProvider(web_search=True)
    assert provider.complete_text(system="system", user="user", timeout_s=300) == "ok"
    assert captured["timeout"] == 300
    assert "web_search=live" in captured["cmd"]

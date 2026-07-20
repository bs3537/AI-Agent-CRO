"""API contract tests for the Healthcare Movers page."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sma_monitor.api.app import create_app
from sma_monitor.healthcare_movers.ranking import compute_mover_rankings
from sma_monitor.healthcare_movers.store import save_ranking_run


def _ranking_result():
    dates = [
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    ]
    universe = [
        {
            "ticker": "VRTX",
            "company_name": "Vertex Pharmaceuticals",
            "industry": "Biotechnology",
            "exchange": "NASDAQ",
            "market_cap": 120_000_000_000,
        },
        {
            "ticker": "MOVE",
            "company_name": "Mover Medical",
            "industry": "Medical Devices",
            "exchange": "NYSE",
            "market_cap": 500_000_000,
        },
    ]
    prices = {
        "VRTX": [
            {"price_date": date, "close": close, "volume": 1_000_000}
            for date, close in zip(dates, [400, 410, 420, 430, 440, 460], strict=True)
        ],
        "MOVE": [
            {"price_date": date, "close": close, "volume": 250_000}
            for date, close in zip(dates, [20, 18, 16, 15, 14, 12], strict=True)
        ],
    }
    return compute_mover_rankings(universe, prices)


def test_healthcare_movers_api_returns_all_windows_and_held_badge():
    save_ranking_run(
        _ranking_result(),
        generated_at=datetime(2090, 7, 18, tzinfo=UTC),
    )

    with TestClient(create_app()) as client:
        response = client.get("/api/healthcare-movers")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "current"
    assert body["source"] == "fmp"
    assert set(body["rankings"]) == {"1", "2", "3", "4", "5"}
    vrtx = body["rankings"]["5"]["gainers"][0]
    assert vrtx["company_name"] == "Vertex Pharmaceuticals"
    assert vrtx["ticker"] == "VRTX"
    assert vrtx["return_pct"] == pytest.approx(15)
    assert vrtx["is_held"] is True
    assert body["rankings"]["5"]["decliners"][0]["ticker"] == "MOVE"


def test_healthcare_movers_api_has_explicit_unavailable_state(monkeypatch):
    monkeypatch.setattr(
        "sma_monitor.api.routes.healthcare_movers.latest_ranking_snapshot",
        lambda: None,
    )

    with TestClient(create_app()) as client:
        response = client.get("/api/healthcare-movers")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["rankings"] == {}

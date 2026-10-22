"""Job-health tracking, daily API call caps and the positions cache."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sma_monitor.api.app import create_app
from sma_monitor.llm import usage_cap
from sma_monitor.orchestrator.job_status import job_statuses, record_job_run


# Module-level API client.
@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


# A recorded success clears the overdue flag; a failure keeps the old success time.
def test_job_status_records_success_and_failure():
    record_job_run("fmp_market_data", True, "ok")
    first = next(j for j in job_statuses() if j["job"] == "fmp_market_data")
    assert first["ok"] is True and first["overdue"] is False
    record_job_run("fmp_market_data", False, "boom")
    second = next(j for j in job_statuses() if j["job"] == "fmp_market_data")
    assert second["ok"] is False
    assert second["last_success_at"] == first["last_success_at"]


# Calls beyond the per-day limit raise instead of spending more.
def test_daily_call_cap_blocks_after_limit(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_DAILY_CALL_LIMIT", "0")
    with pytest.raises(usage_cap.UsageCapExceeded):
        usage_cap.record_call("perplexity")


# /api/jobs lists all three jobs.
def test_jobs_endpoint(client):
    body = client.get("/api/jobs").json()
    assert {j["job"] for j in body["jobs"]} == {"ibkr_pull", "fmp_market_data", "catalyst_search"}


# Manual positions are flagged and the grid is cached until a write happens.
def test_positions_flag_manual_only(client):
    r = client.post("/api/positions", params={"offline": True},
                    json={"ticker": "ZMAN", "portfolio_weight_pct": 1.0, "thesis": "x"})
    assert r.status_code == 201
    rows = client.get("/api/positions").json()["positions"]
    assert next(p for p in rows if p["ticker"] == "ZMAN")["is_manual_only"] is True

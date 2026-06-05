from __future__ import annotations

import json

from sma_monitor.db import connection
from sma_monitor.orchestrator.runner import process_runner_requests
from sma_monitor.orchestrator.store import (
    claim_next_runner_request,
    enqueue_runner_request,
    finish_runner_request,
    init_orchestrator_schema,
    recent_runner_requests,
)


def _clear_runner_requests() -> None:
    init_orchestrator_schema()
    with connection() as conn:
        conn.execute("DELETE FROM runner_requests")


def test_runner_request_lifecycle():
    _clear_runner_requests()
    req = enqueue_runner_request(
        command="manual_recompute_one",
        ticker="VRTX",
        payload={"compute_source": "test"},
    )
    assert req["status"] == "queued"

    claimed = claim_next_runner_request()
    assert claimed is not None
    assert claimed["request_id"] == req["request_id"]
    assert claimed["status"] == "running"
    assert json.loads(claimed["payload_json"])["compute_source"] == "test"

    finish_runner_request(req["request_id"], summary={"ok": True})
    rows = recent_runner_requests(limit=1)
    assert rows[0]["request_id"] == req["request_id"]
    assert rows[0]["status"] == "succeeded"
    assert json.loads(rows[0]["summary_json"])["ok"] is True


def test_runner_processor_dispatches_manual_recompute_one(monkeypatch):
    _clear_runner_requests()
    req = enqueue_runner_request(
        command="manual_recompute_one",
        ticker="VRTX",
        payload={"compute_source": "hermes_test", "offline": True},
    )
    captured = {}

    def fake_recompute(ticker, *, offline, compute_source):
        captured.update({"ticker": ticker, "offline": offline, "compute_source": compute_source})
        return {"decisions": {"decided": 1}}

    monkeypatch.setattr(
        "sma_monitor.orchestrator.manual_recompute.recompute_one_with_refresh",
        fake_recompute,
    )

    summary = process_runner_requests(limit=1)
    assert summary["succeeded"] == 1
    assert captured == {"ticker": "VRTX", "offline": True, "compute_source": "hermes_test"}
    row = recent_runner_requests(limit=1)[0]
    assert row["request_id"] == req["request_id"]
    assert row["status"] == "succeeded"


def test_runner_processor_records_failure(monkeypatch):
    _clear_runner_requests()
    req = enqueue_runner_request(command="manual_recompute_all", payload={"force": True})

    def fail_recompute(*, offline, force):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "sma_monitor.orchestrator.manual_recompute.recompute_all_with_refresh",
        fail_recompute,
    )

    summary = process_runner_requests(limit=1)
    assert summary["failed"] == 1
    row = recent_runner_requests(limit=1)[0]
    assert row["request_id"] == req["request_id"]
    assert row["status"] == "failed"
    assert "boom" in row["error"]

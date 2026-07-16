from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

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


def test_runner_claim_prioritizes_chat_over_older_backend_jobs():
    _clear_runner_requests()
    base = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    enqueue_runner_request(
        command="manual_recompute_all",
        payload={"force": True},
        requested_at=base,
    )
    enqueue_runner_request(
        command="manual_recompute_one",
        ticker="META",
        payload={"compute_source": "test"},
        requested_at=base + timedelta(seconds=1),
    )
    chat = enqueue_runner_request(
        command="chat_complete",
        payload={"message": "Prioritize my dashboard chat"},
        requested_at=base + timedelta(minutes=5),
    )

    claimed = claim_next_runner_request()

    assert claimed is not None
    assert claimed["request_id"] == chat["request_id"]
    assert claimed["command"] == "chat_complete"


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


def test_runner_processor_dispatches_preliminary_thesis_one(monkeypatch):
    _clear_runner_requests()
    req = enqueue_runner_request(
        command="preliminary_thesis_one",
        ticker="VRTX",
        payload={
            "upgrade_existing_ai": True,
            "refresh_inputs": True,
            "compute_source": "hermes_test_preliminary",
        },
    )
    captured = {}

    def fake_workflow(**kwargs):
        captured.update(kwargs)
        return {"drafts": {"created": 1, "failed": []}}

    monkeypatch.setattr(
        "sma_monitor.orchestrator.preliminary_thesis.run_preliminary_thesis_workflow",
        fake_workflow,
    )

    summary = process_runner_requests(limit=1)

    assert summary["succeeded"] == 1
    assert captured["tickers"] == ["VRTX"]
    assert captured["upgrade_existing_ai"] is True
    assert captured["refresh_inputs"] is True
    assert captured["compute_source"] == "hermes_test_preliminary"
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


# A dashboard recompute can return a summary with internal decision errors when
# the LLM/rating stage failed for the ticker. That must not be marked as a
# successful runner request, because the tile would keep showing the old rating.
def test_runner_processor_fails_manual_recompute_when_decision_stage_errors(monkeypatch):
    _clear_runner_requests()
    req = enqueue_runner_request(
        command="manual_recompute_one",
        ticker="PRAX",
        payload={"compute_source": "hermes_test", "offline": False},
    )

    def fake_recompute(ticker, *, offline, compute_source):
        return {
            "tickers": [ticker],
            "scoring": {"scored": 0, "errors": 0, "skipped": 0},
            "decisions": {"decided": 0, "skipped": 0, "errors": 1, "holdings": 1},
        }

    monkeypatch.setattr(
        "sma_monitor.orchestrator.manual_recompute.recompute_one_with_refresh",
        fake_recompute,
    )

    summary = process_runner_requests(limit=1)

    assert summary["succeeded"] == 0
    assert summary["failed"] == 1
    row = recent_runner_requests(limit=1)[0]
    assert row["request_id"] == req["request_id"]
    assert row["status"] == "failed"
    assert "decision" in row["error"].lower()


def test_runner_processor_dispatches_chat_completion(monkeypatch):
    _clear_runner_requests()
    enqueue_runner_request(
        command="chat_complete",
        payload={
            "message": "What changed for VRTX?",
            "history": [],
            "include_portfolio": True,
            "attachment_context": "(no uploaded files in this turn)",
            "attachments": [],
        },
    )

    class FakeChatProvider:
        model_label = "codex-cli"

        def complete_text(self, *, system, user, max_tokens=600):
            assert "USER QUESTION" in user
            assert "What changed for VRTX?" in user
            return "VPS Codex chat response."

        def complete_json(self, **kwargs):
            return {}

    monkeypatch.setattr("sma_monitor.chat.service.get_provider", lambda **kw: FakeChatProvider())

    summary = process_runner_requests(limit=1)

    assert summary["succeeded"] == 1
    row = recent_runner_requests(limit=1)[0]
    assert row["command"] == "chat_complete"
    assert row["status"] == "succeeded"
    result = json.loads(row["summary_json"])
    assert result["answer"] == "VPS Codex chat response."
    assert result["model_used"] == "codex-cli"

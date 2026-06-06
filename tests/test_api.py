"""W5 tests — FastAPI backend.

Drives the app via FastAPI's TestClient against the isolated sandbox DB set up
in conftest.py (a copy of the repo's seeded data/). Covers the grid, detail,
thesis edit round-trip, multipart upload, synchronous recompute, and status.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sma_monitor.api.app import create_app

# A ticker present in the seeded sandbox data with a sidecar (a held position).
HELD = "VRTX"


# Single client for the module; `with` ensures startup events (ensure_dirs +
# init_db) fire against the sandbox.
@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


# Liveness probe.
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_dashboard_role_does_not_start_background_loops(monkeypatch):
    import asyncio

    import sma_monitor.api.app as app_mod
    from sma_monitor.config import settings

    started = {"scheduler": False, "ibkr": False}

    async def fake_scheduler():
        started["scheduler"] = True
        await asyncio.sleep(0)

    async def fake_ibkr():
        started["ibkr"] = True
        await asyncio.sleep(0)

    monkeypatch.setattr(settings, "sma_deployment_role", "dashboard")
    monkeypatch.setattr(app_mod, "_scheduler_loop", fake_scheduler)
    monkeypatch.setattr(app_mod, "_ibkr_sync_loop", fake_ibkr)

    with TestClient(app_mod.create_app()) as c:
        assert c.get("/api/health").status_code == 200

    assert started == {"scheduler": False, "ibkr": False}


# The A/B/C/D rubric: verdict sets the band; within a hold, bear severity then
# confidence split it (A strong hold, B/C hold, D sell).
def test_grade_rubric():
    from sma_monitor.api.routes.positions import _grade

    assert _grade("sell", 0.9, 0) == "D"      # sell is always D
    assert _grade("watch", 0.9, 0) == "C"     # watch is the weakest hold
    assert _grade("hold", 0.9, 5) == "C"      # held, but a serious bear case
    assert _grade("hold", 0.9, 3) == "B"      # moderate bear case
    assert _grade("hold", 0.50, 0) == "B"     # low confidence
    assert _grade("hold", 0.9, 2) == "A"      # clean, confident hold
    assert _grade("hold", 0.60, 0) == "A"     # confidence exactly at threshold


# The grid returns held positions with P&L derived from market_value − cost_basis.
def test_list_positions(client):
    r = client.get("/api/positions")
    assert r.status_code == 200
    body = r.json()
    tickers = {p["ticker"] for p in body["positions"]}
    assert HELD in tickers
    vrtx = next(p for p in body["positions"] if p["ticker"] == HELD)
    if vrtx["cost_basis"] is not None:
        assert vrtx["open_pnl"] == pytest.approx(vrtx["market_value"] - vrtx["cost_basis"])
    if vrtx["rating"] is not None:
        assert vrtx["rating"]["action"] == ("sell" if vrtx["rating"]["grade"] == "D" else "hold")
    if vrtx["spark"] is not None:
        assert set(vrtx["spark"]) >= {"closes", "ema20", "technical_state"}


# Detail includes the evidence trail (scored articles from the seed data).
def test_get_detail(client):
    r = client.get(f"/api/positions/{HELD}")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == HELD
    assert isinstance(body["scores"], list)
    assert "files" in body and "red_team" in body and "catalysts" in body


# An unknown ticker is a 404, not an empty 200.
def test_detail_404(client):
    assert client.get("/api/positions/ZZZZ").status_code == 404


# Editing the thesis round-trips: the PUT response and a fresh GET both reflect it.
def test_put_thesis_roundtrip(client):
    new = "Updated thesis via API test — durable franchise."
    r = client.put(f"/api/positions/{HELD}/thesis", json={"thesis": new})
    assert r.status_code == 200 and r.json()["thesis"] == new
    assert client.get(f"/api/positions/{HELD}").json()["thesis"] == new


# Uploading a thesis doc stores it, extracts text, and surfaces it in detail.
def test_upload_file(client):
    files = {"file": ("apitest.md", b"# Thesis\n\nKey driver is the launch ramp.", "text/markdown")}
    r = client.post(f"/api/positions/{HELD}/files", files=files)
    assert r.status_code == 201
    rec = r.json()
    assert rec["filename"] == "apitest.md" and rec["n_chars"] > 0
    listed = {f["filename"] for f in client.get(f"/api/positions/{HELD}").json()["files"]}
    assert "apitest.md" in listed


# An unsupported file type is rejected with 415.
def test_upload_unsupported_type(client):
    files = {"file": ("logo.png", b"\x89PNG\r\n", "image/png")}
    assert client.post(f"/api/positions/{HELD}/files", files=files).status_code == 415


# Synchronous recompute returns a fresh decision with a valid verdict.
def test_recompute_wait(client):
    r = client.post(f"/api/positions/{HELD}/recompute", params={"wait": True, "offline": True})
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] is False
    assert body["rating"]["grade"] in {"A", "B", "C", "D"}
    assert body["rating"]["compute_source"] == "manual_single"
    assert body["rating"]["action"] == ("sell" if body["rating"]["grade"] == "D" else "hold")
    assert body["rating"]["technical_state"] in {
        "above_ema20",
        "below_ema20",
        "extended_below_ema20",
        "no_price_data",
    }
    assert body["decision"]["verdict"] in {"hold", "watch", "sell"}
    assert body["decision"]["color"] in {"green", "yellow", "red"}
    assert body["decision"]["compute_source"] == "manual_single"


def test_recompute_dashboard_role_enqueues(client, monkeypatch):
    from sma_monitor.api.routes.positions import settings
    from sma_monitor.db import connection

    with connection() as conn:
        conn.execute("DELETE FROM runner_requests")
    monkeypatch.setattr(settings, "sma_deployment_role", "dashboard")

    r = client.post(f"/api/positions/{HELD}/recompute")
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] is True
    assert body["request_id"]
    assert body["queue_status"] == "queued"

    status = client.get("/api/status").json()
    queued = [q for q in status["runner_requests"] if q["request_id"] == body["request_id"]]
    assert queued and queued[0]["command"] == "manual_recompute_one"


def test_recompute_all_dashboard_role_enqueues(client, monkeypatch):
    from sma_monitor.api.routes.positions import settings
    from sma_monitor.db import connection

    with connection() as conn:
        conn.execute("DELETE FROM runner_requests")
    monkeypatch.setattr(settings, "sma_deployment_role", "dashboard")

    r = client.post("/api/positions/recompute", params={"force": True})
    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] is True
    assert body["request_id"]
    assert body["queue_status"] == "queued"


def test_add_manual_position_runs_analysis(client):
    r = client.post(
        "/api/positions",
        params={"offline": True},
        json={
            "ticker": "MANU",
            "portfolio_weight_pct": 1.25,
            "thesis": "Manual position thesis for API test.",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["position"]["ticker"] == "MANU"
    assert body["position"]["pct_nav"] == pytest.approx(0.0125)
    assert body["position"]["rating"]["grade"] in {"A", "B", "C", "D"}
    assert body["position"]["rating"]["compute_source"] == "manual_single"

    tickers = {p["ticker"] for p in client.get("/api/positions").json()["positions"]}
    assert "MANU" in tickers


def test_chat_portfolio_context(client, monkeypatch):
    captured = {}

    class FakeChatProvider:
        model_label = "fake-chat"

        def complete_text(self, *, system, user, max_tokens=600):
            captured["user"] = user
            return "VRTX is present in the saved dashboard context."

        def complete_json(self, **kwargs):
            return {}

    monkeypatch.setattr(
        "sma_monitor.api.routes.chat.get_provider", lambda **kw: FakeChatProvider()
    )
    r = client.post(
        "/api/chat",
        data={"message": "What is the latest CRO view on VRTX?", "history": "[]"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("VRTX")
    assert body["model_used"] == "fake-chat"
    assert "VRTX" in body["used_tickers"]
    assert "CURRENT DASHBOARD PORTFOLIO SNAPSHOT" in captured["user"]


def test_chat_upload_text_file(client, monkeypatch):
    captured = {}

    class FakeChatProvider:
        model_label = "fake-chat"

        def complete_text(self, *, system, user, max_tokens=600):
            captured["user"] = user
            return "I read the uploaded file."

        def complete_json(self, **kwargs):
            return {}

    monkeypatch.setattr("sma_monitor.api.routes.chat.get_provider", lambda **kw: FakeChatProvider())
    files = {"files": ("note.md", b"# Note\nAQST thesis update.", "text/markdown")}
    r = client.post("/api/chat", data={"message": "Read this note", "history": "[]"}, files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["attachments"][0]["filename"] == "note.md"
    assert body["attachments"][0]["parser"] == "local_text"
    assert "AQST thesis update" in captured["user"]


def test_dashboard_chat_enqueues_vps_runner_request(client, monkeypatch):
    from sma_monitor.config import settings
    from sma_monitor.db import connection
    from sma_monitor.orchestrator.store import init_orchestrator_schema, recent_runner_requests

    init_orchestrator_schema()
    with connection() as conn:
        conn.execute("DELETE FROM runner_requests")
    monkeypatch.setattr(settings, "sma_deployment_role", "dashboard")

    r = client.post(
        "/api/chat",
        data={"message": "Use the VPS Codex runner for VRTX chat", "history": "[]"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["scheduled"] is True
    assert body["command"] == "chat_complete"
    req = recent_runner_requests(limit=1)[0]
    assert req["request_id"] == body["request_id"]
    assert req["command"] == "chat_complete"
    payload = json.loads(req["payload_json"])
    assert payload["message"] == "Use the VPS Codex runner for VRTX chat"
    assert payload["include_portfolio"] is True


def test_chat_status_returns_completed_runner_result(client):
    from sma_monitor.db import connection
    from sma_monitor.orchestrator.store import (
        enqueue_runner_request,
        finish_runner_request,
        init_orchestrator_schema,
    )

    init_orchestrator_schema()
    with connection() as conn:
        conn.execute("DELETE FROM runner_requests")
    req = enqueue_runner_request(command="chat_complete", payload={"message": "hello"})
    finish_runner_request(
        req["request_id"],
        summary={
            "answer": "VPS Codex answered.",
            "model_used": "codex-cli",
            "used_tickers": ["VRTX"],
            "cited_context": [],
            "data_freshness": {},
            "attachments": [],
        },
    )

    r = client.get(f"/api/chat/{req['request_id']}")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["result"]["answer"] == "VPS Codex answered."
    assert body["result"]["model_used"] == "codex-cli"


# Dashboard recompute is queued to the VPS runner. The frontend needs a safe
# status endpoint so it can wait until the LLM rating has actually landed (or
# surface a runner failure) before telling the PM the tile is updated.
def test_recompute_status_returns_completed_runner_summary(client):
    from sma_monitor.db import connection
    from sma_monitor.orchestrator.store import (
        enqueue_runner_request,
        finish_runner_request,
        init_orchestrator_schema,
    )

    init_orchestrator_schema()
    with connection() as conn:
        conn.execute("DELETE FROM runner_requests")
    req = enqueue_runner_request(command="manual_recompute_one", ticker=HELD, payload={})
    finish_runner_request(
        req["request_id"],
        summary={
            "tickers": [HELD],
            "decisions": {"decided": 1, "skipped": 0, "errors": 0, "holdings": 1},
        },
    )

    r = client.get(f"/api/positions/recompute/{req['request_id']}")

    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] == req["request_id"]
    assert body["status"] == "succeeded"
    assert body["ticker"] == HELD
    assert body["refresh"]["decisions"]["decided"] == 1
    assert body["error"] is None


def test_recompute_status_returns_failed_runner_error(client):
    from sma_monitor.db import connection
    from sma_monitor.orchestrator.store import (
        enqueue_runner_request,
        finish_runner_request,
        init_orchestrator_schema,
    )

    init_orchestrator_schema()
    with connection() as conn:
        conn.execute("DELETE FROM runner_requests")
    req = enqueue_runner_request(command="manual_recompute_one", ticker=HELD, payload={})
    finish_runner_request(req["request_id"], error="decision stage failed: 1 error")

    r = client.get(f"/api/positions/recompute/{req['request_id']}")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert "decision stage failed" in body["error"]


def test_thesis_word_limit(client):
    too_long = "word " * 2001
    r = client.put(f"/api/positions/{HELD}/thesis", json={"thesis": too_long})
    assert r.status_code == 422


# Status returns the operational snapshot with a position count.
def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "spend" in body and "degrade" in body
    assert body["positions"]["count"] >= 1


def test_delete_holding_removes_tile_and_stored_data(client):
    ticker = "MRNA"
    before = client.get("/api/positions").json()["positions"]
    if ticker not in {p["ticker"] for p in before}:
        pytest.skip(f"{ticker} not present in seeded latest pull")

    r = client.delete(f"/api/positions/{ticker}")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == ticker
    assert body["deleted"]["positions"] >= 1

    after = client.get("/api/positions").json()["positions"]
    assert ticker not in {p["ticker"] for p in after}
    assert client.get(f"/api/positions/{ticker}").status_code == 404

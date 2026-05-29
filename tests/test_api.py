"""W5 tests — FastAPI backend.

Drives the app via FastAPI's TestClient against the isolated sandbox DB set up
in conftest.py (a copy of the repo's seeded data/). Covers the grid, detail,
thesis edit round-trip, multipart upload, synchronous recompute, and status.
"""
from __future__ import annotations

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
    assert body["decision"]["verdict"] in {"hold", "watch", "sell"}
    assert body["decision"]["color"] in {"green", "yellow", "red"}


# Status returns the operational snapshot with a position count.
def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "spend" in body and "degrade" in body
    assert body["positions"]["count"] >= 1

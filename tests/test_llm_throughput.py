"""W9 tests — LLM throughput tiering, bounded concurrency, and 429 backoff.

In-process, no real subprocess: stage model/effort resolution + concurrency
knobs are pure env functions; the codex retry loop is exercised by
monkeypatching subprocess.run; map_concurrent is checked for ordering, error
capture, and actual parallelism; and run_decisions is run end-to-end through
the concurrent path against the sandbox DB with a fake provider.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


# --- Tiering: per-stage effort/model resolution ----------------------------

# Default efforts come from DEFAULT_EFFORT; a global env override applies to
# every stage; a stage-specific override beats the global one.
def test_stage_effort_defaults_and_overrides(monkeypatch):
    from sma_monitor.llm.throughput import DEFAULT_EFFORT, stage_effort
    for s in DEFAULT_EFFORT:
        monkeypatch.delenv(f"SMA_CODEX_EFFORT_{s.upper()}", raising=False)
    monkeypatch.delenv("SMA_CODEX_EFFORT", raising=False)
    assert stage_effort("scorer") == "medium"
    assert stage_effort("decision") == "high"
    monkeypatch.setenv("SMA_CODEX_EFFORT", "low")  # global
    assert stage_effort("decision") == "low"
    monkeypatch.setenv("SMA_CODEX_EFFORT_DECISION", "high")  # stage-specific wins
    assert stage_effort("decision") == "high"


# Model precedence: stage-specific override → global SMA_CODEX_MODEL → None.
def test_stage_model_precedence(monkeypatch):
    from sma_monitor.llm.throughput import stage_model
    monkeypatch.delenv("SMA_CODEX_MODEL", raising=False)
    monkeypatch.delenv("SMA_CODEX_MODEL_SCORER", raising=False)
    assert stage_model("scorer") is None
    monkeypatch.setenv("SMA_CODEX_MODEL", "global-m")
    assert stage_model("scorer") == "global-m"
    monkeypatch.setenv("SMA_CODEX_MODEL_SCORER", "scorer-m")
    assert stage_model("scorer") == "scorer-m"


# Concurrency parses from env and clamps to [1, MAX_CONCURRENCY]; junk → default.
def test_llm_concurrency_parsing(monkeypatch):
    from sma_monitor.llm.throughput import (
        DEFAULT_CONCURRENCY,
        MAX_CONCURRENCY,
        llm_concurrency,
    )
    monkeypatch.delenv("SMA_LLM_CONCURRENCY", raising=False)
    assert llm_concurrency() == DEFAULT_CONCURRENCY
    monkeypatch.setenv("SMA_LLM_CONCURRENCY", "8")
    assert llm_concurrency() == 8
    monkeypatch.setenv("SMA_LLM_CONCURRENCY", "0")
    assert llm_concurrency() == 1
    monkeypatch.setenv("SMA_LLM_CONCURRENCY", "999")
    assert llm_concurrency() == MAX_CONCURRENCY
    monkeypatch.setenv("SMA_LLM_CONCURRENCY", "abc")
    assert llm_concurrency() == DEFAULT_CONCURRENCY


# --- map_concurrent ---------------------------------------------------------

# Results come back in INPUT order; a raising item yields (None, exception)
# rather than aborting the batch.
def test_map_concurrent_preserves_order_and_captures_errors():
    from sma_monitor.llm.throughput import map_concurrent

    def fn(x):
        if x == 3:
            raise ValueError("boom")
        return x * 10

    res = map_concurrent(fn, [1, 2, 3, 4], workers=4)
    assert [r for r, _ in res] == [10, 20, None, 40]
    assert res[0][1] is None
    assert isinstance(res[2][1], ValueError)


# With workers>1 the calls overlap, so wall-clock is well under the sequential
# sum — proof the thread pool actually parallelizes the (subprocess-like) work.
def test_map_concurrent_runs_in_parallel():
    from sma_monitor.llm.throughput import map_concurrent

    def fn(x):
        time.sleep(0.1)
        return x

    t0 = time.perf_counter()
    res = map_concurrent(fn, [1, 2, 3, 4], workers=4)
    elapsed = time.perf_counter() - t0
    assert [r for r, _ in res] == [1, 2, 3, 4]
    assert elapsed < 0.35  # 4×0.1s sequential would be 0.4s


# --- Stage-aware provider construction --------------------------------------

# get_provider(stage=...) builds a Codex provider carrying that stage's model +
# effort; with no stage it returns the bare account-default provider.
def test_get_provider_stage_sets_model_and_effort(monkeypatch):
    import sma_monitor.llm.provider as provider_mod
    monkeypatch.setattr("sma_monitor.llm.codex_client.codex_available", lambda: True)
    monkeypatch.setenv("SMA_CODEX_MODEL_SCORER", "gpt-5.5")
    monkeypatch.setenv("SMA_CODEX_EFFORT_SCORER", "low")
    p = provider_mod.get_provider(stage="scorer")
    assert p is not None and p.model == "gpt-5.5" and p.effort == "low"
    bare = provider_mod.get_provider()
    assert bare.model is None and bare.effort is None
    assert provider_mod.get_provider(prefer_offline=True) is None


# --- Codex CLI flag injection + 429 backoff ---------------------------------

# _run injects -m <model> and -c model_reasoning_effort=<effort> right after the
# `exec` subcommand.
def test_run_injects_model_and_effort_flags(monkeypatch):
    from sma_monitor.llm import codex_client as cc
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    out = cc._run(["exec", "-"], "prompt", model="gpt-5.5", effort="high")
    assert out == "ok"
    cmd = captured["cmd"]
    assert cmd[1] == "exec"
    assert "-m" in cmd and "gpt-5.5" in cmd
    assert "-c" in cmd and "model_reasoning_effort=high" in cmd


# A rate-limited exec is retried with (zeroed) backoff and then succeeds.
def test_run_retries_on_rate_limit_then_succeeds(monkeypatch):
    from sma_monitor.llm import codex_client as cc
    monkeypatch.setenv("SMA_LLM_BACKOFF_BASE_S", "0")
    monkeypatch.setenv("SMA_LLM_MAX_RETRIES", "4")
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="stream error: 429 Too Many Requests",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    assert cc._run(["exec", "-"], "p") == "ok"
    assert calls["n"] == 3


# After exhausting the retry budget on persistent rate-limits, _run raises.
def test_run_gives_up_after_max_retries(monkeypatch):
    from sma_monitor.llm import codex_client as cc
    from sma_monitor.llm.provider import LLMError
    monkeypatch.setenv("SMA_LLM_BACKOFF_BASE_S", "0")
    monkeypatch.setenv("SMA_LLM_MAX_RETRIES", "2")
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="429 rate limit exceeded")

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    with pytest.raises(LLMError):
        cc._run(["exec", "-"], "p")
    assert calls["n"] == 3  # initial try + 2 retries


# A non-rate-limit failure is NOT retried — it raises on the first attempt.
def test_run_does_not_retry_non_rate_limit(monkeypatch):
    from sma_monitor.llm import codex_client as cc
    from sma_monitor.llm.provider import LLMError
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="some other failure")

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    with pytest.raises(LLMError):
        cc._run(["exec", "-"], "p")
    assert calls["n"] == 1


# --- End-to-end concurrent decision run -------------------------------------

# In-process fake provider returning a fixed verdict — thread-safe (stateless).
class _FakeProvider:
    model_label = "fake-llm"

    def complete_json(self, *, system, user, schema=None, max_tokens=512) -> dict:
        return {"verdict": "hold",
                "note": "line1\nline2\nline3\nline4",
                "drivers": ["d"], "confidence": 0.5}

    def complete_text(self, *, system, user, max_tokens=600) -> str:
        return "x"


# run_decisions drives the concurrent (workers>1) compute path end-to-end over
# the sandbox holdings and persists one decision per holding with no errors.
def test_run_decisions_concurrent_path(monkeypatch):
    import sma_monitor.decision.engine as eng
    monkeypatch.setenv("SMA_LLM_CONCURRENCY", "4")
    monkeypatch.setattr(eng, "get_provider", lambda **kw: _FakeProvider())
    monkeypatch.setattr("sma_monitor.orchestrator.cost.record_llm_call", lambda **kw: None)
    res = eng.run_decisions(offline=False, force=True)
    assert res["holdings"] >= 1
    assert res["decided"] == res["holdings"]
    assert res["errors"] == 0

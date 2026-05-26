"""W1 tests — LLM provider abstraction + Codex CLI client.

Two layers:
  - subprocess: drive the real CodexProvider against a stub `codex` (tests/stub_codex.py)
    to prove complete_json/complete_text actually shell out and parse correctly.
  - in-process: a fake provider exercises score_with_llm / red_team_with_llm parsing +
    catalog enrichment without any subprocess.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

STUB = REPO / "tests" / "stub_codex.py"


# Make the stub a self-contained executable codex shim and point env at it.
@pytest.fixture
def stub_codex(monkeypatch):
    wrapper = REPO / "tests" / ".codex_shim"
    wrapper.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{STUB}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("SMA_CODEX_BIN", str(wrapper))
    yield
    wrapper.unlink(missing_ok=True)


# codex_available() is True once the stub binary is on the configured path.
def test_codex_available_with_stub(stub_codex):
    from sma_monitor.llm.codex_client import codex_available
    assert codex_available() is True


# get_provider() returns a CodexProvider when the stub is present, and honors
# prefer_offline by returning None.
def test_get_provider_selects_codex(stub_codex):
    from sma_monitor.llm import get_provider
    assert get_provider() is not None
    assert get_provider(prefer_offline=True) is None


# complete_json shells out to the stub and returns a schema-conforming object.
def test_complete_json_against_stub(stub_codex):
    from sma_monitor.llm.codex_client import CodexProvider
    from sma_monitor.scorer.claude_client import AXIS_SCHEMA
    data = CodexProvider().complete_json(system="sys", user="usr", schema=AXIS_SCHEMA)
    assert set(AXIS_SCHEMA["required"]).issubset(data.keys())
    # Validates into the real schema model used by the scorer.
    from sma_monitor.scorer.schema import AxisScores
    axes = AxisScores.model_validate(data)
    assert 0 <= axes.confidence <= 1


# complete_text returns the stub's free-text paragraph (narrative path).
def test_complete_text_against_stub(stub_codex):
    from sma_monitor.llm.codex_client import CodexProvider
    out = CodexProvider().complete_text(system="sys", user="usr")
    assert "paragraph" in out.lower()


# A non-existent codex binary → not available → callers fall back to heuristic.
def test_unavailable_without_binary(monkeypatch):
    monkeypatch.setenv("SMA_CODEX_BIN", "/nonexistent/codex-xyz")
    from sma_monitor.llm.codex_client import codex_available
    assert codex_available() is False


# An in-process fake provider returning fixed JSON — used to test the scorer
# and red-team adapters without a subprocess.
class _FakeProvider:
    model_label = "fake-llm"

    def __init__(self, payload: dict):
        self._payload = payload

    def complete_json(self, *, system, user, schema=None, max_tokens=512) -> dict:
        return dict(self._payload)

    def complete_text(self, *, system, user, max_tokens=600) -> str:
        return "fake narrative"


# score_with_llm validates the provider's JSON into AxisScores and labels the row.
def test_score_with_llm_parses_and_labels():
    from sma_monitor.scorer.claude_client import score_with_llm
    from sma_monitor.scorer.schema import ScoreCandidate
    provider = _FakeProvider({
        "financial_impact": 7.0, "narrative_shift": 6.0, "time_criticality": 5.0,
        "rationale": "test", "confidence": 0.8,
    })
    cand = ScoreCandidate(
        article_event_id="a1", title="t", excerpt="e", source=None, source_tier=None,
        published_at=None, ticker="VRTX", pct_nav=0.24, conviction_tier=5,
        stage="commercial_stage", thesis="th", nearest_catalyst_days=10,
        primary_bucket_id=2, primary_bucket_name="Regulatory",
        primary_bucket_confidence=0.8, secondary_buckets=[],
    )
    axes, model = score_with_llm(cand, provider=provider)
    assert model == "fake-llm"
    assert axes.financial_impact == 7.0 and axes.confidence == 0.8


# red_team_with_llm drops fabricated warning-sign ids and enriches real ones.
def test_red_team_with_llm_enriches_and_drops_fabricated():
    from sma_monitor.red_team.catalog import load_catalog
    from sma_monitor.red_team.claude_client import red_team_with_llm
    from sma_monitor.red_team.schema import RedTeamCandidate
    catalog = load_catalog()
    real_id = catalog.warning_signs[0].id
    provider = _FakeProvider({
        "bearish_thesis": "concern",
        "matched_warning_signs": [
            {"id": real_id, "fit_strength": 0.6},
            {"id": "totally_made_up_id", "fit_strength": 0.9},
        ],
        "matched_buckets": [4],
        "severity_of_concern": 3,
        "invalidator": "x",
        "confidence": 0.5,
    })
    cand = RedTeamCandidate(
        score_event_id="s1", article_event_id="a1", title="t", excerpt="e",
        source=None, source_tier=None, published_at=None, ticker="VRTX",
        company_name="Vertex", pct_nav=0.24, conviction_tier=5,
        stage="commercial_stage", thesis="th", nearest_catalyst_days=10,
        primary_bucket_id=4, primary_bucket_name="Commercial", composite=12.0,
        threshold_band="t2_to_t", scorer_axes=(7.0, 6.0, 5.0),
        scorer_rationale="r", scorer_confidence=0.7,
    )
    result, model = red_team_with_llm(cand, catalog, provider=provider)
    assert model == "fake-llm"
    ids = [m.id for m in result.matched_warning_signs]
    assert real_id in ids and "totally_made_up_id" not in ids  # fabricated dropped
    assert result.matched_warning_signs[0].name  # enriched from catalog

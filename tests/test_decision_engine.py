"""W3 tests — thesis-drift decision engine.

In-process, no subprocess and no DB: exercise the verdict logic, the sev≥4
guard, the LLM-path parsing, and inputs_hash staleness directly against the
real schema models. (The full sandbox-DB end-to-end is covered by the offline
verification recipe.)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sma_monitor.decision.engine import (  # noqa: E402
    DECISION_OUTPUT_SCHEMA,
    decide,
    decide_with_grade,
    decision_inputs_hash,
    thesis_hash,
)
from sma_monitor.decision.schema import (  # noqa: E402
    BearEvidence,
    DecisionCandidate,
    ScoreEvidence,
)


# Neutralize the lazy cost-ledger write in the LLM path so tests never touch
# the live cost DB. The import in _decide_llm reads the patched name at call.
@pytest.fixture(autouse=True)
def _no_cost_ledger(monkeypatch):
    monkeypatch.setattr(
        "sma_monitor.orchestrator.cost.record_llm_call",
        lambda **kw: None,
    )


# Build a minimal candidate; callers override the fields a test cares about.
def _candidate(**over) -> DecisionCandidate:
    base = dict(
        ticker="VRTX", company_name="Vertex", stage="commercial_stage",
        conviction_tier=5, thesis="Durable franchise on CF; pipeline optional.",
        pct_nav=0.24, market_value=240_000.0, cost_basis=200_000.0,
        open_pnl=40_000.0, pnl_pct=0.2, nearest_catalyst_days=None,
        has_overdue_catalyst=False, catalysts=[], scores=[], bears=[],
        max_severity=1, max_composite=0.0,
    )
    base.update(over)
    return DecisionCandidate(**base)


def _bear(sev: int) -> BearEvidence:
    return BearEvidence(
        pass_event_id=f"p{sev}", title="t", bearish_thesis="bear",
        severity_of_concern=sev, matched_patterns=["channel_inventory_build"],
        invalidator="next print clears inventory",
    )


def _score(comp: float) -> ScoreEvidence:
    return ScoreEvidence(
        score_event_id=f"s{comp}", title="t", primary_bucket_id=4,
        composite=comp, threshold_band="above_t" if comp >= 15 else "t2_to_t",
        rationale="r",
    )


# A quiet, profitable holding with no evidence resolves to HOLD/green.
def test_heuristic_hold_when_quiet():
    d = decide(_candidate(), "th", "ih", provider=None)
    assert d.verdict == "hold" and d.color == "green"
    assert d.model_used == "heuristic-v1"
    assert d.note and len(d.note.splitlines()) >= 4  # 4–5 line note


# A severe red-team concern (severity 5) drives SELL/red.
def test_heuristic_sell_on_severe_bear():
    c = _candidate(bears=[_bear(5)], max_severity=5)
    d = decide(c, "th", "ih", provider=None)
    assert d.verdict == "sell" and d.color == "red"


# An above-T scored article with no severe bear still escalates to WATCH.
def test_heuristic_watch_on_alert_band_score():
    c = _candidate(scores=[_score(18.0)], max_composite=18.0)
    d = decide(c, "th", "ih", provider=None)
    assert d.verdict == "watch" and d.color == "yellow"


# LLM-final mode: severe deterministic/red-team inputs are prompt context, but
# a valid LLM grade remains the final decision.
def test_llm_grade_is_authoritative_even_with_sev4():
    c = _candidate(bears=[_bear(4)], max_severity=4)
    provider = _FakeProvider({"llm_grade": "A", "note": "looks fine",
                              "drivers": [], "confidence": 0.9})
    d = decide(c, "th", "ih", provider=provider)
    assert d.verdict == "hold" and d.color == "green"
    assert "Auto-escalated" not in d.note


# The LLM path parses the provider's JSON into a PositionDecision and labels it.
def test_llm_path_parses_and_labels():
    c = _candidate(bears=[_bear(2)], max_severity=2)
    provider = _FakeProvider({"llm_grade": "D", "note": "thesis broken",
                              "drivers": ["failed endpoint"], "confidence": 0.77})
    d, llm_grade = decide_with_grade(c, "th", "ih", provider=provider)
    assert d.verdict == "sell" and d.color == "red"
    assert llm_grade == "D"
    assert d.model_used == "fake-llm" and d.confidence == 0.77
    assert d.drivers == ["failed endpoint"]


# An unparseable grade falls back to the deterministic path.
def test_llm_bad_grade_uses_deterministic_fallback():
    provider = _FakeProvider({"llm_grade": "?", "note": "n", "drivers": [], "confidence": 0.5})
    d = decide(_candidate(), "th", "ih", provider=provider)
    assert d.verdict == "hold"
    assert d.model_used == "heuristic-v1"


# inputs_hash changes when a new score id enters the evidence set — the signal
# run_decisions uses to re-compute a holding.
def test_inputs_hash_changes_with_evidence():
    th = thesis_hash("VRTX", "thesis")
    base = dict(ticker="VRTX", thesis_h=th, pct_nav=0.24,
                nearest_catalyst_days=None, pass_ids=[])
    h1 = decision_inputs_hash(score_ids=["s1"], **base)
    h2 = decision_inputs_hash(score_ids=["s1", "s2"], **base)
    assert h1 != h2
    # Order-insensitive: same set in any order hashes identically.
    h3 = decision_inputs_hash(score_ids=["s2", "s1"], **base)
    assert h2 == h3


# Editing the thesis changes thesis_hash and therefore inputs_hash.
def test_thesis_edit_changes_inputs_hash():
    common = dict(ticker="VRTX", pct_nav=0.24, nearest_catalyst_days=None,
                  score_ids=["s1"], pass_ids=[])
    h_old = decision_inputs_hash(thesis_h=thesis_hash("VRTX", "old"), **common)
    h_new = decision_inputs_hash(thesis_h=thesis_hash("VRTX", "new"), **common)
    assert h_old != h_new


# The output schema the provider is constrained to asks Codex for a structured
# grade assessment; action/verdict/color are derived by the engine.
def test_output_schema_shape():
    props = DECISION_OUTPUT_SCHEMA["properties"]
    assert "color" not in props
    assert "verdict" not in props
    assert set(DECISION_OUTPUT_SCHEMA["required"]) == {
        "llm_grade",
        "thesis_clause_impacts",
        "hard_breaker",
        "technical_assessment",
        "note",
        "drivers",
        "confidence",
    }
    assert props["llm_grade"]["enum"] == ["A", "B", "C", "D"]
    assert "technical_assessment" in props


# In-process fake provider returning fixed JSON — no subprocess.
class _FakeProvider:
    model_label = "fake-llm"

    def __init__(self, payload: dict):
        self._payload = payload

    def complete_json(self, *, system, user, schema=None, max_tokens=512) -> dict:
        return dict(self._payload)

    def complete_text(self, *, system, user, max_tokens=600) -> str:
        return "fake narrative"

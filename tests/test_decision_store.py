"""Decision/rating persistence behavior for manual recompute."""
from __future__ import annotations

from datetime import UTC, datetime

from sma_monitor.decision.schema import PositionDecision, PositionRating
from sma_monitor.decision.store import latest_decision, latest_rating, save_decision, save_rating


def test_save_decision_replaces_same_input_hash():
    first = PositionDecision(
        ticker="ZZUP",
        verdict="hold",
        color="green",
        note="old note",
        drivers=["old"],
        confidence=0.4,
        thesis_hash="th",
        inputs_hash="same-inputs",
        model_used="heuristic-v1",
        decided_at=datetime(2035, 1, 1, tzinfo=UTC),
    )
    second = first.model_copy(
        update={
            "note": "new note",
            "drivers": ["new"],
        "confidence": 0.8,
        "model_used": "codex-cli",
        "compute_source": "manual_single",
        "decided_at": datetime(2035, 1, 2, tzinfo=UTC),
        }
    )

    save_decision(first, decision_version="test-upsert")
    save_decision(second, decision_version="test-upsert")

    row = latest_decision("ZZUP")
    assert row["note"] == "new note"
    assert row["confidence"] == 0.8
    assert row["model_used"] == "codex-cli"
    assert row["compute_source"] == "manual_single"


def test_save_rating_replaces_same_input_hash():
    first = PositionRating(
        ticker="ZZUR",
        action="hold",
        grade="B",
        attention_state="monitor",
        risk_score=20.0,
        risk_components={"technical_trend": 12.0},
        latest_close=10.0,
        ema20=11.0,
        price_vs_ema20_pct=-0.09,
        technical_state="extended_below_ema20",
        deterministic_grade="B",
        llm_grade="B",
        final_grade="B",
        note="old rating",
        drivers=["old"],
        confidence=0.5,
        thesis_hash="th",
        inputs_hash="same-inputs",
        model_used="codex-cli",
        rating_version="test-upsert",
        decided_at=datetime(2035, 1, 1, tzinfo=UTC),
    )
    second = first.model_copy(
        update={
            "grade": "A",
            "attention_state": "clean",
            "risk_score": 8.0,
            "llm_grade": "A",
            "final_grade": "A",
            "note": "new rating",
            "drivers": ["new"],
            "confidence": 0.9,
            "compute_source": "manual_all",
            "decided_at": datetime(2035, 1, 2, tzinfo=UTC),
        }
    )

    save_rating(first)
    save_rating(second)

    row = latest_rating("ZZUR")
    assert row["grade"] == "A"
    assert row["note"] == "new rating"
    assert row["confidence"] == 0.9
    assert row["compute_source"] == "manual_all"

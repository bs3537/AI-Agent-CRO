"""V2 rating-engine tests: grade/action sync plus EMA20 guardrails."""
from __future__ import annotations

from sma_monitor.decision.rating import rate_candidate
from sma_monitor.decision.schema import DecisionCandidate, TechnicalSnapshot


def _candidate(**over) -> DecisionCandidate:
    base = dict(
        ticker="VRTX", company_name="Vertex", stage="commercial_stage",
        conviction_tier=5, thesis="Durable franchise on CF; pipeline optional.",
        pct_nav=0.24, market_value=240_000.0, cost_basis=200_000.0,
        open_pnl=40_000.0, pnl_pct=0.2, nearest_catalyst_days=None,
        has_overdue_catalyst=False, catalysts=[], scores=[], bears=[],
        max_severity=1, max_composite=0.0, technical=None,
    )
    base.update(over)
    return DecisionCandidate(**base)


def _rate(c: DecisionCandidate, *, llm_grade: str | None = None):
    return rate_candidate(
        c,
        thesis_h="th",
        inputs_h="ih",
        note="note",
        drivers=[],
        confidence=0.7,
        model_used="test",
        llm_grade=llm_grade,
    )


def _below_tech(points: int = 12) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        latest_close=8.0,
        latest_ema20=10.0,
        price_vs_ema20_pct=-0.2,
        consecutive_below_ema20=4,
        ema20_slope_5d=-0.05,
        technical_state="extended_below_ema20",
        risk_points=points,
    )


def test_quiet_holding_is_hold_a():
    r = _rate(_candidate())

    assert r.grade == "A"
    assert r.action == "hold"
    assert r.attention_state == "clean"


def test_placeholder_thesis_caps_a_at_b():
    r = _rate(_candidate(thesis="PLACEHOLDER thesis. Replace later."))

    assert r.grade == "B"
    assert r.action == "hold"


def test_below_ema20_alone_caps_a_at_b_not_d():
    r = _rate(_candidate(technical=_below_tech()))

    assert r.grade == "B"
    assert r.action == "hold"
    assert r.technical_state == "extended_below_ema20"


def test_ten_percent_unrealized_loss_is_warning_not_auto_sell():
    r = _rate(_candidate(open_pnl=-22_500.0, pnl_pct=-0.1125))

    assert r.grade == "B"
    assert r.action == "hold"
    assert r.risk_components["unrealized_loss"] == 10.0
    assert "open P/L -11.2% warning" in r.drivers


def test_severity_five_forces_sell_d():
    r = _rate(_candidate(max_severity=5))

    assert r.grade == "D"
    assert r.action == "sell"


def test_fundamental_pressure_plus_ema_weakness_can_reach_d():
    r = _rate(
        _candidate(
            max_severity=4,
            max_composite=18.0,
            has_overdue_catalyst=True,
            technical=_below_tech(points=15),
        )
    )

    assert r.grade == "D"
    assert r.action == "sell"


def test_llm_a_overrides_deterministic_b():
    r = _rate(
        _candidate(
            thesis="PLACEHOLDER thesis. Replace later.",
            technical=_below_tech(),
        ),
        llm_grade="A",
    )

    assert r.deterministic_grade == "B"
    assert r.llm_grade == "A"
    assert r.grade == "A"
    assert r.action == "hold"


def test_llm_d_overrides_quiet_deterministic_a():
    r = _rate(_candidate(), llm_grade="D")

    assert r.deterministic_grade == "A"
    assert r.llm_grade == "D"
    assert r.grade == "D"
    assert r.action == "sell"

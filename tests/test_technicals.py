"""Technical indicator tests for the V2 rating layer."""
from __future__ import annotations

import pytest

from sma_monitor.decision.technicals import ema, technical_state


def test_ema20_seed_and_next_values():
    values = [float(i) for i in range(1, 26)]
    out = ema(values, window=20)

    assert out[:19] == [None] * 19
    assert out[19] == pytest.approx(10.5)
    assert out[20] == pytest.approx(11.5)
    assert out[-1] == pytest.approx(15.5)


def test_short_series_returns_no_price_data():
    snap = technical_state([1.0, 2.0, 3.0])

    assert snap.technical_state == "no_price_data"
    assert snap.risk_points == 0
    assert snap.latest_ema20 is None


def test_above_rising_ema20_is_low_risk():
    snap = technical_state([float(i) for i in range(1, 31)])

    assert snap.technical_state == "above_ema20"
    assert snap.price_vs_ema20_pct is not None
    assert snap.price_vs_ema20_pct > 0
    assert snap.risk_points == 0


def test_extended_below_ema20_is_flagged():
    closes = [float(i) for i in range(1, 31)] + [10.0, 9.0, 8.0]
    snap = technical_state(closes)

    assert snap.technical_state == "extended_below_ema20"
    assert snap.consecutive_below_ema20 >= 3
    assert snap.risk_points >= 12

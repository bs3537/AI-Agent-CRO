"""Technical indicators used by the thesis-rating layer.

The first technical signal is deliberately simple and auditable: daily close
versus the 20-day EMA. It is an attention/risk input, not a standalone thesis
breaker.
"""
from __future__ import annotations

from .schema import TechnicalSnapshot

EMA_WINDOW = 20


def ema(values: list[float], window: int = EMA_WINDOW) -> list[float | None]:
    """Return an EMA series aligned to values, seeded by the first window SMA."""
    if window <= 0:
        raise ValueError("window must be positive")
    clean = [float(v) for v in values]
    if len(clean) < window:
        return [None] * len(clean)

    out: list[float | None] = [None] * len(clean)
    seed = sum(clean[:window]) / window
    out[window - 1] = seed
    alpha = 2 / (window + 1)
    prev = seed
    for i in range(window, len(clean)):
        prev = clean[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def technical_state(closes: list[float] | None) -> TechnicalSnapshot:
    """Classify the latest close relative to EMA20 and assign risk points."""
    clean = [float(c) for c in (closes or []) if c is not None]
    ema20 = ema(clean)
    if len(clean) < EMA_WINDOW or not ema20 or ema20[-1] is None:
        return TechnicalSnapshot(closes=clean, ema20=ema20)

    latest_close = clean[-1]
    latest_ema = float(ema20[-1])
    distance = (latest_close - latest_ema) / latest_ema if latest_ema else None
    consecutive_below = _consecutive_below(clean, ema20)
    slope = _ema_slope_5d(ema20)

    state = "above_ema20"
    if latest_close < latest_ema:
        state = "extended_below_ema20" if consecutive_below >= 3 else "below_ema20"

    snapshot = TechnicalSnapshot(
        closes=clean,
        ema20=[round(v, 6) if v is not None else None for v in ema20],
        latest_close=round(latest_close, 6),
        latest_ema20=round(latest_ema, 6),
        price_vs_ema20_pct=round(distance, 6) if distance is not None else None,
        consecutive_below_ema20=consecutive_below,
        ema20_slope_5d=round(slope, 6) if slope is not None else None,
        technical_state=state,
    )
    snapshot.risk_points = technical_risk_points(snapshot)
    return snapshot


def technical_risk_points(snapshot: TechnicalSnapshot) -> int:
    """Map EMA20 status to a bounded rating-risk add-on."""
    if snapshot.technical_state == "no_price_data":
        return 0
    if snapshot.technical_state == "above_ema20":
        return 0 if (snapshot.ema20_slope_5d or 0) > 0 else 3
    if (
        snapshot.price_vs_ema20_pct is not None
        and snapshot.price_vs_ema20_pct <= -0.05
        and (snapshot.ema20_slope_5d or 0) < 0
    ):
        return 15
    if snapshot.technical_state == "extended_below_ema20":
        return 12
    return 8


def _consecutive_below(closes: list[float], ema_values: list[float | None]) -> int:
    count = 0
    for close, ema_value in zip(reversed(closes), reversed(ema_values), strict=False):
        if ema_value is None or close >= ema_value:
            break
        count += 1
    return count


def _ema_slope_5d(ema_values: list[float | None]) -> float | None:
    latest = ema_values[-1] if ema_values else None
    if latest is None or len(ema_values) < 6:
        return None
    prior = ema_values[-6]
    if prior in (None, 0):
        return None
    return (latest - prior) / prior

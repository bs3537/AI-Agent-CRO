"""Build the joined Holding view (Position ⨝ Sidecar).

This is the canonical input every downstream phase reads. A position with no
sidecar is reported but excluded — Phase 3 can't score a holding it has no
conviction/stage/thesis context for.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from .schema import Catalyst, Holding, Position, Sidecar
from .sidecar import load_all_sidecars
from .store import latest_positions


def join(
    positions: Sequence[Position],
    sidecars: dict[str, Sidecar],
    *,
    today: date | None = None,
) -> tuple[list[Holding], list[str]]:
    """Returns (holdings, tickers_missing_sidecar)."""
    today = today or date.today()
    holdings: list[Holding] = []
    missing: list[str] = []
    for p in positions:
        sc = sidecars.get(p.ticker)
        if sc is None:
            missing.append(p.ticker)
            continue
        unresolved = [c for c in sc.catalysts if not c.resolved]
        nearest, overdue = _proximity(unresolved, today)
        holdings.append(
            Holding(
                ticker=p.ticker,
                qty=p.qty,
                market_value=p.market_value,
                pct_nav=p.pct_nav,
                cost_basis=p.cost_basis,
                pulled_at=p.pulled_at,
                nav=p.nav,
                conviction_tier=sc.conviction_tier,
                stage=sc.stage,
                thesis=sc.thesis,
                company_name=sc.company_name,
                aliases=sc.aliases,
                brands=sc.brands,
                products=sc.products,
                indications=sc.indications,
                catalysts=unresolved,
                nearest_catalyst_days=nearest,
                has_overdue_catalyst=overdue,
            )
        )
    return holdings, missing


def _proximity(catalysts: list[Catalyst], today: date) -> tuple[int | None, bool]:
    future_days = [(c.date - today).days for c in catalysts if (c.date - today).days >= 0]
    overdue = any((c.date - today).days < 0 for c in catalysts)
    return (min(future_days) if future_days else None), overdue


def latest_joined(
    today: date | None = None,
) -> tuple[list[Holding], list[str], datetime | None]:
    positions, pulled_at = latest_positions()
    holdings, missing = join(positions, load_all_sidecars(), today=today)
    return holdings, missing, pulled_at

"""Read-only API for the latest complete healthcare-mover ranking."""
from __future__ import annotations

from fastapi import APIRouter

from ...healthcare_movers.store import latest_ranking_snapshot
from ...portfolio.joined import latest_joined
from ..schemas import HealthcareMoversResponse

router = APIRouter(prefix="/api", tags=["healthcare-movers"])


@router.get("/healthcare-movers", response_model=HealthcareMoversResponse)
def get_healthcare_movers() -> HealthcareMoversResponse:
    snapshot = latest_ranking_snapshot()
    if snapshot is None:
        return HealthcareMoversResponse(
            status="unavailable",
            rankings={},
            message="The first healthcare universe refresh has not completed yet.",
        )

    holdings, _missing, _pulled_at = latest_joined()
    held_tickers = {holding.ticker.upper() for holding in holdings}
    rankings = {
        window: {
            direction: [
                {**row, "is_held": str(row["ticker"]).upper() in held_tickers}
                for row in rows
            ]
            for direction, rows in directions.items()
        }
        for window, directions in snapshot["rankings"].items()
    }
    return HealthcareMoversResponse(
        status="current",
        as_of_date=snapshot["as_of_date"],
        generated_at=snapshot["generated_at"],
        universe_count=snapshot["universe_count"],
        covered_count=snapshot["covered_count"],
        coverage_fraction=snapshot["coverage_fraction"],
        rankings=rankings,
    )

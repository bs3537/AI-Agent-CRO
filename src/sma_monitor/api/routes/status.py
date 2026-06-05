"""Status route (Workstream 5).

GET /api/status — the same operational snapshot the orchestrator `status` CLI
prints (spend vs budget, degrade cascade, spend by kind, active flags, dead
letters) plus a position-count summary, returned as JSON for the dashboard.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from ...config import settings
from ...orchestrator import dead_letter as dl
from ...orchestrator.cost import current_degrade_state
from ...orchestrator.flags import get_active_flags
from ...orchestrator.store import cost_by_kind_since, recent_runner_requests
from ...portfolio.joined import latest_joined
from ..schemas import StatusOut

router = APIRouter(prefix="/api", tags=["status"])


# GET /api/status — wrap the orchestrator helpers into one JSON snapshot.
@router.get("/status", response_model=StatusOut)
def get_status() -> StatusOut:
    degrade = current_degrade_state()
    midnight = (
        datetime.now(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )
    by_kind = [dict(r) for r in cost_by_kind_since(midnight)]
    flags = list(get_active_flags())
    pending = dl.pending()
    recent = dl.all_recent(limit=20)
    holdings, missing, pulled_at = latest_joined()

    return StatusOut(
        spend={
            "spent_usd": degrade.spent_usd,
            "budget_usd": degrade.budget_usd,
            "fraction_spent": degrade.fraction_spent,
        },
        degrade={
            "skip_red_team_t2_t_band": degrade.skip_red_team_t2_t_band,
            "skip_opus_narrative": degrade.skip_opus_narrative,
            "drop_buckets_10_11": degrade.drop_buckets_10_11,
            "drop_bucket_12": degrade.drop_bucket_12,
        },
        spend_by_kind=by_kind,
        flags=[dict(f) for f in flags],
        dead_letters={
            "pending": len(pending),
            "recent": [dict(r) for r in recent],
        },
        positions={
            "count": len(holdings),
            "pulled_at": pulled_at.isoformat() if pulled_at else None,
            "missing_sidecars": missing,
        },
        deployment={"role": settings.deployment_role()},
        runner_requests=recent_runner_requests(limit=10),
    )

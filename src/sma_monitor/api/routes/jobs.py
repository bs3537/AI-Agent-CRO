"""GET /api/jobs — health of the scheduled data jobs for the dashboard header."""
from __future__ import annotations

from fastapi import APIRouter

from ...orchestrator.job_status import job_statuses

router = APIRouter(prefix="/api", tags=["jobs"])


# Last success/failure and overdue flag for IBKR, FMP and catalyst jobs.
@router.get("/jobs")
def get_jobs() -> dict:
    return {"jobs": job_statuses()}

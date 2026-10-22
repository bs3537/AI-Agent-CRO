"""Scheduled-job health: last success/failure per job, shown on the dashboard."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ..config import settings
from ..db import connection

log = logging.getLogger("sma_monitor.orchestrator.job_status")

JOB_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    job            TEXT PRIMARY KEY,
    last_run_at    TEXT NOT NULL,
    last_success_at TEXT,
    ok             INTEGER NOT NULL,
    detail         TEXT
);
"""

# Job id -> (display label, max hours since last success before it counts as overdue).
JOBS: dict[str, tuple[str, float]] = {
    "ibkr_pull": ("IBKR holdings", 26.0),
    "fmp_market_data": ("FMP prices & targets", 26.0),
    "catalyst_search": ("Catalysts", 24.0 * 8),
}


# Create the job_runs table if needed.
def init_job_status_schema() -> None:
    with connection() as conn:
        conn.executescript(JOB_RUNS_SCHEMA)


# Record one run outcome; a success also stamps last_success_at.
def record_job_run(job: str, ok: bool, detail: str = "") -> None:
    init_job_status_schema()
    now = datetime.now(UTC).isoformat()
    with connection() as conn:
        row = conn.execute(
            "SELECT last_success_at FROM job_runs WHERE job = ?", (job,)
        ).fetchone()
        last_success = now if ok else (row["last_success_at"] if row else None)
        conn.execute(
            "INSERT OR REPLACE INTO job_runs(job, last_run_at, last_success_at, ok, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (job, now, last_success, 1 if ok else 0, detail[:500]),
        )
    if not ok:
        _email_failure(job, detail)


# Email the failure to the configured Resend recipients (no-op without a key).
def _email_failure(job: str, detail: str) -> None:
    if not settings.resend_api_key or not settings.resend_email_to:
        return
    from ..outputs.channels import ResendChannel, _split_recipients

    try:
        ResendChannel(
            api_key=settings.resend_api_key,
            from_addr=settings.resend_email_from or "",
            to_addrs=_split_recipients(settings.resend_email_to),
        ).send_alert(
            "SYSTEM",
            f"Aegis CRO job failed: {JOBS.get(job, (job, 0))[0]}\n\n{detail[:1500]}",
        )
    except Exception:  # noqa: BLE001 - alert delivery must never break the job.
        log.exception("job_failure_email_failed", extra={"job": job})


# Current status of every scheduled job, with an overdue flag.
def job_statuses() -> list[dict]:
    init_job_status_schema()
    with connection() as conn:
        rows = {r["job"]: r for r in conn.execute("SELECT * FROM job_runs").fetchall()}
    now = datetime.now(UTC)
    out: list[dict] = []
    for job, (label, max_hours) in JOBS.items():
        row = rows.get(job)
        last_success = row["last_success_at"] if row else None
        overdue = last_success is None or (
            now - datetime.fromisoformat(last_success) > timedelta(hours=max_hours)
        )
        out.append({
            "job": job,
            "label": label,
            "last_run_at": row["last_run_at"] if row else None,
            "last_success_at": last_success,
            "ok": bool(row["ok"]) if row else None,
            "detail": (row["detail"] if row else None) or None,
            "overdue": overdue,
        })
    return out

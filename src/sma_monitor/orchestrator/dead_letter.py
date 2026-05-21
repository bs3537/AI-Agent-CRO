"""Scorer / red-team dead-letter policy.

PLAN.MD §6: "Scorer model error → retry once, then dead-letter and flag."

record_failure   — bumps attempt_count; status = 'pending' on first failure,
                   'abandoned' on second. Caller should set the
                   scorer_dead_letter / red_team_dead_letter system flag
                   when status becomes 'abandoned'.
clear_on_success — remove the dead-letter row once a retry succeeds.
pending          — list rows that should be retried on next cycle.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .flags import set_flag
from .store import (
    all_dead_letters,
    clear_dead_letter,
    pending_dead_letters,
    record_dead_letter,
)


# Record a per-(kind, article, ticker) failure. First failure → 'pending';
# second → 'abandoned' + a system flag. Returns the new state for logging.
def record_failure(
    *,
    kind: str,
    article_event_id: str | None,
    ticker: str | None,
    error: str,
) -> dict:
    """Returns {'event_id', 'attempt_count', 'status'}."""
    failed_at = datetime.now(timezone.utc)
    eid = record_dead_letter(
        kind=kind, article_event_id=article_event_id, ticker=ticker,
        error=error, failed_at=failed_at,
    )
    # Re-read to get current state (post-update)
    for row in all_dead_letters(limit=200):
        if row["event_id"] == eid:
            if row["status"] == "abandoned":
                set_flag(
                    f"{kind}_dead_letter",
                    metadata={"event_id": eid, "ticker": ticker,
                              "article_event_id": article_event_id,
                              "error": error[:200]},
                )
            return {"event_id": eid, "attempt_count": row["attempt_count"],
                    "status": row["status"]}
    return {"event_id": eid, "attempt_count": 1, "status": "pending"}


# Remove a dead-letter row after a retry succeeds. Called from each
# pipeline immediately after a successful (re)run.
def clear_on_success(event_id_str: str) -> None:
    clear_dead_letter(event_id_str)


# List dead-letter rows still in 'pending' state. Used by the retry CLI.
def pending(kind: str | None = None) -> list[dict]:
    return pending_dead_letters(kind=kind)


# List recent dead-letter rows (pending + abandoned) for the status CLI.
def all_recent(limit: int = 30) -> list[dict]:
    return all_dead_letters(limit=limit)

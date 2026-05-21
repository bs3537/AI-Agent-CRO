"""System flags (PLAN.MD §6 failure-handling).

Named flags that get set when the orchestrator detects a degraded condition
and surface in the next digest. Each flag should clear automatically once
the condition resolves; if a flag never clears, that's a tuning signal.

Conventional flag names:
  stale_positions     — Flex Query refresh failed or pull is > N hours old
  exa_failure         — News pipeline saw repeated Exa errors
  budget_degraded     — Today's spend has crossed the first cascade threshold
  scorer_dead_letter  — One or more scored pairs failed even after retry
  red_team_dead_letter — Same, for red team
"""
from __future__ import annotations

from datetime import datetime, timezone

from .store import clear_flag as _clear, list_active_flags, set_flag as _set


# Activate a named flag, attaching optional metadata. Replaces any prior
# value for the same flag and stamps the current time as set_at.
def set_flag(name: str, *, metadata: dict | None = None) -> None:
    _set(name, metadata=metadata, set_at=datetime.now(timezone.utc))


# Mark a flag inactive. Stamps cleared_at; the row stays for audit.
def clear_flag(name: str) -> None:
    _clear(name, cleared_at=datetime.now(timezone.utc))


# Return every currently-active flag for the digest footer and the status CLI.
def get_active_flags() -> list[dict]:
    """For digest footer + status CLI."""
    return list_active_flags()

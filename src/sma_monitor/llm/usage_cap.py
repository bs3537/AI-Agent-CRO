"""Per-day call caps for paid LLM and search APIs (protects against runaway loops)."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from ..db import connection

USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_usage (
    day   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);
"""

# Default daily call limits per API kind; override with <KIND>_DAILY_CALL_LIMIT env vars.
DEFAULT_LIMITS = {"openrouter": 3000, "perplexity": 2000}


class UsageCapExceeded(RuntimeError):
    """Raised when today's call limit for an API kind is used up."""


# Today's call limit for *kind* (env override, else default).
def daily_limit(kind: str) -> int:
    raw = os.environ.get(f"{kind.upper()}_DAILY_CALL_LIMIT")
    try:
        return int(raw) if raw else DEFAULT_LIMITS[kind]
    except ValueError:
        return DEFAULT_LIMITS[kind]


# Count one call against today's budget (New York day); raises when over the cap.
def record_call(kind: str) -> None:
    day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    with connection() as conn:
        conn.executescript(USAGE_SCHEMA)
        row = conn.execute(
            "SELECT calls FROM api_usage WHERE day = ? AND kind = ?", (day, kind)
        ).fetchone()
        calls = int(row["calls"]) if row else 0
        if calls >= daily_limit(kind):
            raise UsageCapExceeded(
                f"daily {kind} call limit reached ({calls}/{daily_limit(kind)})"
            )
        conn.execute(
            "INSERT OR REPLACE INTO api_usage(day, kind, calls) VALUES (?, ?, ?)",
            (day, kind, calls + 1),
        )

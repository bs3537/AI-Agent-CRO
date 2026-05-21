"""Phase 6 persistence.

cost_ledger    — one row per Claude API call. Phase 7 reads it for
                 token-per-bucket analytics.
system_flags   — current operational flags. Digest footer reads active ones.
                 Examples: 'stale_positions', 'exa_failure', 'budget_degraded'.
dead_letters   — failed scorer / red-team calls. Retry policy is "retry once,
                 then abandon and flag" per PLAN §6.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from ..db import connection
from ..identity import event_id

# DDL for the Phase 6 tables. Three independent tables: cost_ledger
# (append-only spend), system_flags (current operational flags),
# dead_letters (failed work + retry state).
ORCHESTRATOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_ledger (
    event_id            TEXT PRIMARY KEY,
    kind                TEXT NOT NULL,            -- 'score' | 'red_team' | 'digest_narrative'
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL,
    related_event_id    TEXT,
    incurred_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_incurred ON cost_ledger(incurred_at);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_kind     ON cost_ledger(kind);

CREATE TABLE IF NOT EXISTS system_flags (
    flag_name      TEXT PRIMARY KEY,
    set_at         TEXT NOT NULL,
    cleared_at     TEXT,
    metadata       TEXT,
    active         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_system_flags_active ON system_flags(active);

CREATE TABLE IF NOT EXISTS dead_letters (
    event_id          TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,                -- 'score' | 'red_team'
    article_event_id  TEXT,
    ticker            TEXT,
    error             TEXT NOT NULL,
    attempt_count     INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'abandoned'
    first_failed_at   TEXT NOT NULL,
    last_attempt_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dead_letters_status ON dead_letters(status);
"""


# Create the Phase 6 tables. Safe to call repeatedly.
def init_orchestrator_schema() -> None:
    with connection() as conn:
        conn.executescript(ORCHESTRATOR_SCHEMA)


# --- cost ledger -------------------------------------------------------------


# Stable id for one cost ledger row. Keyed on (kind, model, incurred_at,
# related_event_id) so duplicate writes within the same millisecond don't
# create duplicate rows.
def cost_event_id(kind: str, model: str, incurred_at: datetime, related_event_id: str | None) -> str:
    return event_id({
        "kind": "cost", "subkind": kind, "model": model,
        "incurred_at": incurred_at.isoformat(),
        "related_event_id": related_event_id,
    })


# Persist one cost ledger row in a single transaction. Used by record_usage
# in cost.py; also by simulate-spend in the orchestrator CLI for cascade tests.
def save_cost_row(
    *,
    kind: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cost_usd: float,
    related_event_id: str | None,
    incurred_at: datetime,
) -> str:
    init_orchestrator_schema()
    eid = cost_event_id(kind, model, incurred_at, related_event_id)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "cost", None, incurred_at.isoformat(),
             json.dumps({"subkind": kind, "model": model, "cost_usd": cost_usd})),
        )
        conn.execute(
            """INSERT OR IGNORE INTO cost_ledger
               (event_id, kind, model, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, cost_usd,
                related_event_id, incurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, kind, model, input_tokens, output_tokens,
             cache_read_tokens, cache_write_tokens, cost_usd,
             related_event_id, incurred_at.isoformat()),
        )
    return eid


# Sum costs and tokens from a given start time. Used to compute today's
# spend (with iso_since = midnight UTC).
def cost_sum_since(iso_since: str) -> dict:
    init_orchestrator_schema()
    with connection() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(cost_usd), 0.0) AS total_cost,
                      COALESCE(SUM(input_tokens), 0) AS total_in,
                      COALESCE(SUM(output_tokens), 0) AS total_out,
                      COUNT(*) AS n_calls
               FROM cost_ledger WHERE incurred_at >= ?""",
            (iso_since,),
        ).fetchone()
    return {"total_cost": row["total_cost"], "total_in": row["total_in"],
            "total_out": row["total_out"], "n_calls": row["n_calls"]}


# Break down spend by kind ('score', 'red_team', 'digest_narrative', ...).
# Feeds the status CLI's "spend by kind" table.
def cost_by_kind_since(iso_since: str) -> list[dict]:
    init_orchestrator_schema()
    with connection() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT kind, COUNT(*) AS n_calls,
                      SUM(cost_usd) AS cost_usd,
                      SUM(input_tokens + output_tokens) AS total_tokens
               FROM cost_ledger WHERE incurred_at >= ?
               GROUP BY kind ORDER BY cost_usd DESC""",
            (iso_since,)).fetchall()]


# --- system flags ------------------------------------------------------------


# Insert or reactivate a flag row. ON CONFLICT updates set_at, metadata,
# and re-activates a previously cleared flag.
def set_flag(flag_name: str, *, metadata: dict | None = None, set_at: datetime) -> None:
    init_orchestrator_schema()
    with connection() as conn:
        conn.execute(
            """INSERT INTO system_flags(flag_name, set_at, cleared_at, metadata, active)
               VALUES (?, ?, NULL, ?, 1)
               ON CONFLICT(flag_name) DO UPDATE SET
                 set_at = excluded.set_at,
                 cleared_at = NULL,
                 metadata = excluded.metadata,
                 active = 1""",
            (flag_name, set_at.isoformat(),
             json.dumps(metadata) if metadata else None),
        )


# Mark a flag inactive. Keeps the row for audit; sets cleared_at.
def clear_flag(flag_name: str, *, cleared_at: datetime) -> None:
    init_orchestrator_schema()
    with connection() as conn:
        conn.execute(
            "UPDATE system_flags SET active = 0, cleared_at = ? WHERE flag_name = ?",
            (cleared_at.isoformat(), flag_name),
        )


# Return every active flag for the digest footer + status CLI.
def list_active_flags() -> list[dict]:
    init_orchestrator_schema()
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM system_flags WHERE active = 1 ORDER BY set_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# --- dead letters ------------------------------------------------------------


# Stable id for a dead-letter row keyed on (kind, article, ticker). Lets
# repeated failures for the same pair update the same row.
def dead_letter_event_id(kind: str, article_event_id: str | None, ticker: str | None) -> str:
    return event_id({
        "kind": "dead_letter", "subkind": kind,
        "article_event_id": article_event_id, "ticker": ticker,
    })


# Insert a new dead-letter row or bump attempt_count on a repeat failure.
# Second failure transitions status to 'abandoned' so the orchestrator can
# stop retrying and set the dead-letter system flag.
def record_dead_letter(
    *,
    kind: str,
    article_event_id: str | None,
    ticker: str | None,
    error: str,
    failed_at: datetime,
) -> str:
    """Insert or bump attempt_count. Returns dead_letter event_id."""
    init_orchestrator_schema()
    eid = dead_letter_event_id(kind, article_event_id, ticker)
    with connection() as conn:
        row = conn.execute(
            "SELECT attempt_count, status FROM dead_letters WHERE event_id = ?", (eid,)
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO dead_letters
                   (event_id, kind, article_event_id, ticker, error,
                    attempt_count, status, first_failed_at, last_attempt_at)
                   VALUES (?, ?, ?, ?, ?, 1, 'pending', ?, ?)""",
                (eid, kind, article_event_id, ticker, error,
                 failed_at.isoformat(), failed_at.isoformat()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (eid, "dead_letter", ticker, failed_at.isoformat(),
                 json.dumps({"subkind": kind})),
            )
        else:
            new_count = row["attempt_count"] + 1
            new_status = "abandoned" if new_count >= 2 else "pending"
            conn.execute(
                """UPDATE dead_letters
                   SET attempt_count = ?, status = ?, last_attempt_at = ?, error = ?
                   WHERE event_id = ?""",
                (new_count, new_status, failed_at.isoformat(), error, eid),
            )
    return eid


# Remove a dead-letter row on successful retry. Called from each pipeline
# right after a successful (re)run.
def clear_dead_letter(event_id_str: str) -> None:
    """Remove on successful retry."""
    init_orchestrator_schema()
    with connection() as conn:
        conn.execute("DELETE FROM dead_letters WHERE event_id = ?", (event_id_str,))


# List 'pending' dead-letter rows for retry-dead-letters. Optional filter
# on kind so the retry CLI can target one pipeline at a time.
def pending_dead_letters(kind: str | None = None) -> list[dict]:
    init_orchestrator_schema()
    sql = "SELECT * FROM dead_letters WHERE status = 'pending'"
    args: Sequence = ()
    if kind:
        sql += " AND kind = ?"
        args = (kind,)
    sql += " ORDER BY first_failed_at ASC"
    with connection() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# List recent dead-letter rows (pending + abandoned) for the status CLI.
def all_dead_letters(limit: int = 50) -> list[dict]:
    init_orchestrator_schema()
    with connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM dead_letters ORDER BY last_attempt_at DESC LIMIT ?", (limit,)
        ).fetchall()]

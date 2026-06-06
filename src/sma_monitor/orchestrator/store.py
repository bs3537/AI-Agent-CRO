"""Phase 6 persistence.

cost_ledger    — one row per Claude API call. Phase 7 reads it for
                 token-per-bucket analytics.
system_flags   — current operational flags. Digest footer reads active ones.
                 Examples: 'stale_positions', 'exa_failure', 'budget_degraded'.
dead_letters   — failed scorer / red-team calls. Retry policy is "retry once,
                 then abandon and flag" per PLAN §6.
runner_requests — dashboard-enqueued work for a trusted Hermes/Codex runner.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

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

CREATE TABLE IF NOT EXISTS runner_requests (
    request_id    TEXT PRIMARY KEY,
    command       TEXT NOT NULL,
    ticker        TEXT,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'queued',
    requested_at  TEXT NOT NULL,
    claimed_at    TEXT,
    finished_at   TEXT,
    summary_json  TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_runner_requests_status_requested
    ON runner_requests(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_runner_requests_status_command_requested
    ON runner_requests(status, command, requested_at);
CREATE INDEX IF NOT EXISTS idx_runner_requests_ticker
    ON runner_requests(ticker);
"""


# Create the Phase 6 tables. Safe to call repeatedly.
def init_orchestrator_schema() -> None:
    with connection() as conn:
        conn.executescript(ORCHESTRATOR_SCHEMA)


# --- cost ledger -------------------------------------------------------------


# Stable id for one cost ledger row. Keyed on (kind, model, incurred_at,
# related_event_id) so duplicate writes within the same millisecond don't
# create duplicate rows.
def cost_event_id(
    kind: str, model: str, incurred_at: datetime, related_event_id: str | None
) -> str:
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


# --- runner requests ----------------------------------------------------------


def runner_request_event_id(command: str, requested_at: datetime, ticker: str | None) -> str:
    return event_id({
        "kind": "runner_request",
        "command": command,
        "ticker": ticker.upper() if ticker else None,
        "requested_at": requested_at.isoformat(),
    })


def enqueue_runner_request(
    *,
    command: str,
    ticker: str | None = None,
    payload: dict | None = None,
    requested_at: datetime | None = None,
) -> dict:
    init_orchestrator_schema()
    requested_at = requested_at or datetime.now(UTC)
    ticker = ticker.upper() if ticker else None
    payload = payload or {}
    rid = runner_request_event_id(command, requested_at, ticker)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                rid,
                "runner_request",
                ticker,
                requested_at.isoformat(),
                json.dumps({"command": command, "ticker": ticker}),
            ),
        )
        conn.execute(
            """INSERT INTO runner_requests
               (request_id, command, ticker, payload_json, status, requested_at)
               VALUES (?, ?, ?, ?, 'queued', ?)""",
            (rid, command, ticker, json.dumps(payload), requested_at.isoformat()),
        )
    return {
        "request_id": rid,
        "command": command,
        "ticker": ticker,
        "status": "queued",
        "requested_at": requested_at.isoformat(),
    }


def claim_next_runner_request(
    *,
    claimed_at: datetime | None = None,
    commands: list[str] | tuple[str, ...] | None = None,
) -> dict | None:
    init_orchestrator_schema()
    claimed_at = claimed_at or datetime.now(UTC)
    command_filter = [c.strip() for c in (commands or []) if c and c.strip()]
    where = "status = 'queued'"
    params: list[str] = []
    if command_filter:
        placeholders = ",".join("?" for _ in command_filter)
        where += f" AND command IN ({placeholders})"
        params.extend(command_filter)
    with connection() as conn:
        row = conn.execute(
            f"""SELECT * FROM runner_requests
               WHERE {where}
               ORDER BY
                 CASE command
                   WHEN 'chat_complete' THEN 0
                   ELSE 10
                 END ASC,
                 requested_at ASC
               LIMIT 1""",
            params,
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """UPDATE runner_requests
               SET status = 'running', claimed_at = ?
               WHERE request_id = ? AND status = 'queued'""",
            (claimed_at.isoformat(), row["request_id"]),
        )
    out = dict(row)
    out["status"] = "running"
    out["claimed_at"] = claimed_at.isoformat()
    return out


def finish_runner_request(
    request_id: str,
    *,
    summary: dict | None = None,
    error: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    init_orchestrator_schema()
    finished_at = finished_at or datetime.now(UTC)
    status = "failed" if error else "succeeded"
    with connection() as conn:
        conn.execute(
            """UPDATE runner_requests
               SET status = ?, finished_at = ?, summary_json = ?, error = ?
               WHERE request_id = ?""",
            (
                status,
                finished_at.isoformat(),
                json.dumps(summary or {}),
                error[:1000] if error else None,
                request_id,
            ),
        )


def requeue_stale_runner_requests(
    *,
    max_age_minutes: int,
    commands: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> int:
    """Move stale running rows back to queued so a later runner can retry them."""
    init_orchestrator_schema()
    if max_age_minutes <= 0:
        return 0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=max_age_minutes)
    command_filter = [c.strip() for c in (commands or []) if c and c.strip()]
    where = "status = 'running' AND claimed_at IS NOT NULL AND claimed_at < ?"
    params: list[str] = [cutoff.isoformat()]
    if command_filter:
        placeholders = ",".join("?" for _ in command_filter)
        where += f" AND command IN ({placeholders})"
        params.extend(command_filter)
    with connection() as conn:
        cur = conn.execute(
            f"""UPDATE runner_requests
                SET status = 'queued',
                    claimed_at = NULL,
                    error = ?
                WHERE {where}""",
            [
                f"requeued stale running request after {max_age_minutes} minutes",
                *params,
            ],
        )
        return int(cur.rowcount or 0)


def recent_runner_requests(*, limit: int = 20) -> list[dict]:
    init_orchestrator_schema()
    with connection() as conn:
        rows = conn.execute(
            """SELECT * FROM runner_requests
               ORDER BY requested_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_runner_request(request_id: str) -> dict | None:
    init_orchestrator_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM runner_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return dict(row) if row is not None else None


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

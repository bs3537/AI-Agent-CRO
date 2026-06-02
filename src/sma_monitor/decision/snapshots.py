"""Daily rating snapshots — reliable day-over-day change detection (W7).

position_ratings is content-addressed (UNIQUE on ticker+inputs_hash+version),
so a quiet holding keeps a single row and a re-run with unchanged evidence just
overwrites decided_at. That makes "what changed since yesterday?" unanswerable
from position_ratings alone. This table fixes that: the morning brief stamps one
immutable row per ticker per ET calendar day, so the next morning can diff
today's live rating against the prior session's snapshot regardless of whether
the underlying evidence churned.

One row per (snapshot_date, ticker). The brief writes today's rows after it has
already diffed against the previous day, and the write is an idempotent upsert so
re-running the brief on the same day is safe.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from ..db import connection
from .store import latest_ratings


# Convert a sqlite3.Row / Turso _NamedRow into a plain dict. Neither supports
# dict(row) directly (both iterate as value tuples), but both expose keys().
def _as_dict(row) -> dict:
    cols = row.keys()
    return {col: row[col] for col in cols}

# DDL for the daily snapshot table. PRIMARY KEY (snapshot_date, ticker) gives one
# immutable grade/action reading per ticker per day; the index speeds the
# "most recent snapshot before today" lookup the diff runs per ticker.
SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS rating_snapshots (
    snapshot_date     TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    action            TEXT NOT NULL,
    grade             TEXT NOT NULL,
    attention_state   TEXT NOT NULL,
    risk_score        REAL,
    note              TEXT,
    drivers           TEXT,
    confidence        REAL,
    rating_event_id   TEXT,
    captured_at       TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON rating_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_snapshots_date   ON rating_snapshots(snapshot_date);
"""


# Create the rating_snapshots table. Safe to call repeatedly.
def init_snapshot_schema() -> None:
    with connection() as conn:
        conn.executescript(SNAPSHOT_SCHEMA)


# Upsert one snapshot row for (snapshot_date, ticker). Exposed for tests and
# back-fills; the brief uses record_daily_snapshots() to capture the whole book.
def upsert_snapshot(
    *,
    snapshot_date: str,
    ticker: str,
    action: str,
    grade: str,
    attention_state: str,
    risk_score: float | None = None,
    note: str | None = None,
    drivers: str | list | None = None,
    confidence: float | None = None,
    rating_event_id: str | None = None,
    captured_at: str | None = None,
) -> None:
    init_snapshot_schema()
    # Accept drivers as either a JSON string (straight from a DB row) or a list.
    drivers_json = drivers if isinstance(drivers, str) or drivers is None else json.dumps(drivers)
    captured_at = captured_at or datetime.now(UTC).isoformat()
    with connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO rating_snapshots
               (snapshot_date, ticker, action, grade, attention_state, risk_score,
                note, drivers, confidence, rating_event_id, captured_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_date, ticker.upper(), action, grade, attention_state, risk_score,
             note, drivers_json, confidence, rating_event_id, captured_at),
        )


# Capture today's whole-book snapshot from the live latest_ratings() view. One
# upsert per ticker; idempotent so re-running the brief on the same day is safe.
# Returns the number of tickers snapshotted.
def record_daily_snapshots(snapshot_date: str) -> int:
    init_snapshot_schema()
    rows = latest_ratings()
    captured_at = datetime.now(UTC).isoformat()
    with connection() as conn:
        for r in rows:
            conn.execute(
                """INSERT OR REPLACE INTO rating_snapshots
                   (snapshot_date, ticker, action, grade, attention_state, risk_score,
                    note, drivers, confidence, rating_event_id, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_date, r["ticker"], r["action"], r["grade"], r["attention_state"],
                 r["risk_score"], r["note"], r["drivers"], r["confidence"],
                 r["event_id"], captured_at),
            )
    return len(rows)


# Return, per ticker, the most recent snapshot strictly BEFORE `before_date`
# (the prior trading session — handles weekend/holiday gaps since it takes each
# ticker's last recorded day, not a fixed -1 day). Keyed by ticker for O(1) diff.
def previous_snapshots(before_date: str) -> dict[str, dict]:
    init_snapshot_schema()
    sql = """
        SELECT s.* FROM rating_snapshots s
        JOIN (
            SELECT ticker, MAX(snapshot_date) AS md
            FROM rating_snapshots
            WHERE snapshot_date < ?
            GROUP BY ticker
        ) p ON s.ticker = p.ticker AND s.snapshot_date = p.md
    """
    with connection() as conn:
        rows = conn.execute(sql, (before_date,)).fetchall()
    return {row["ticker"]: _as_dict(row) for row in rows}


# Snapshot rows for one specific day, keyed by ticker. Used by tests and any
# audit/back-fill path that wants to inspect a captured session verbatim.
def snapshots_on(snapshot_date: str) -> dict[str, dict]:
    init_snapshot_schema()
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM rating_snapshots WHERE snapshot_date = ?",
            (snapshot_date,),
        ).fetchall()
    return {row["ticker"]: _as_dict(row) for row in rows}

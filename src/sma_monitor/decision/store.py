"""Workstream 3 persistence.

position_decisions — one row per (ticker, inputs_hash, decision_version).
A fresh thesis edit or new ingested evidence changes inputs_hash, producing a
new row while prior decisions stay queryable for audit. latest_decisions()
exposes the most recent decision per ticker — what the dashboard and the 9 AM
email read.
"""
from __future__ import annotations

import json

from ..db import connection
from ..identity import event_id
from .schema import PositionDecision, PositionRating

# DDL for the position_decisions table. UNIQUE on (ticker, inputs_hash,
# decision_version) makes re-running idempotent while a changed thesis or
# evidence set (new inputs_hash) writes a fresh row rather than overwriting.
DECISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_decisions (
    event_id          TEXT PRIMARY KEY,
    ticker            TEXT NOT NULL,
    verdict           TEXT NOT NULL,
    color             TEXT NOT NULL,
    note              TEXT NOT NULL,
    drivers           TEXT,
    confidence        REAL NOT NULL,
    thesis_hash       TEXT NOT NULL,
    inputs_hash       TEXT NOT NULL,
    model_used        TEXT NOT NULL,
    compute_source    TEXT NOT NULL DEFAULT 'unknown',
    decision_version  TEXT NOT NULL,
    decided_at        TEXT NOT NULL,
    UNIQUE (ticker, inputs_hash, decision_version)
);

CREATE INDEX IF NOT EXISTS idx_decisions_ticker     ON position_decisions(ticker);
CREATE INDEX IF NOT EXISTS idx_decisions_verdict    ON position_decisions(verdict);
CREATE INDEX IF NOT EXISTS idx_decisions_decided_at ON position_decisions(decided_at);

CREATE TABLE IF NOT EXISTS position_ratings (
    event_id              TEXT PRIMARY KEY,
    ticker                TEXT NOT NULL,
    action                TEXT NOT NULL,
    grade                 TEXT NOT NULL,
    attention_state       TEXT NOT NULL,
    risk_score            REAL NOT NULL,
    risk_components       TEXT NOT NULL,
    latest_close          REAL,
    ema20                 REAL,
    price_vs_ema20_pct    REAL,
    technical_state       TEXT NOT NULL,
    deterministic_grade   TEXT NOT NULL,
    llm_grade             TEXT,
    final_grade           TEXT NOT NULL,
    note                  TEXT NOT NULL,
    drivers               TEXT,
    confidence            REAL NOT NULL,
    thesis_hash           TEXT NOT NULL,
    inputs_hash           TEXT NOT NULL,
    model_used            TEXT NOT NULL,
    compute_source        TEXT NOT NULL DEFAULT 'unknown',
    rating_version        TEXT NOT NULL,
    decided_at            TEXT NOT NULL,
    UNIQUE (ticker, inputs_hash, rating_version)
);

CREATE INDEX IF NOT EXISTS idx_ratings_ticker     ON position_ratings(ticker);
CREATE INDEX IF NOT EXISTS idx_ratings_grade      ON position_ratings(grade);
CREATE INDEX IF NOT EXISTS idx_ratings_decided_at ON position_ratings(decided_at);
"""


# Create the position_decisions table. Safe to call repeatedly.
def init_decision_schema() -> None:
    with connection() as conn:
        conn.executescript(DECISION_SCHEMA)
        _ensure_column(
            conn,
            "position_decisions",
            "compute_source",
            "TEXT NOT NULL DEFAULT 'unknown'",
        )
        _ensure_column(
            conn,
            "position_ratings",
            "compute_source",
            "TEXT NOT NULL DEFAULT 'unknown'",
        )


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# Stable id for a decision row, keyed on (ticker, inputs_hash, decision_version).
def decision_event_id(ticker: str, inputs_hash: str, decision_version: str) -> str:
    return event_id({
        "kind": "decision",
        "ticker": ticker,
        "inputs_hash": inputs_hash,
        "decision_version": decision_version,
    })


def rating_event_id(ticker: str, inputs_hash: str, rating_version: str) -> str:
    return event_id({
        "kind": "rating",
        "ticker": ticker,
        "inputs_hash": inputs_hash,
        "rating_version": rating_version,
    })


# Persist one PositionDecision in a single transaction. Upserts on
# (ticker, inputs_hash, decision_version): a manual recompute with identical
# inputs can replace the note/confidence/model output, while changed inputs
# still create a distinct audit row.
def save_decision(decision: PositionDecision, *, decision_version: str) -> str:
    init_decision_schema()
    eid = decision_event_id(decision.ticker, decision.inputs_hash, decision_version)
    with connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "decision", decision.ticker, decision.decided_at.isoformat(),
             json.dumps({
                 "verdict": decision.verdict,
                 "color": decision.color,
                 "confidence": decision.confidence,
                 "compute_source": decision.compute_source,
             })),
        )
        conn.execute(
            """INSERT OR REPLACE INTO position_decisions
               (event_id, ticker, verdict, color, note, drivers, confidence,
                thesis_hash, inputs_hash, model_used, compute_source,
                decision_version, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, decision.ticker, decision.verdict, decision.color,
                decision.note, json.dumps(decision.drivers), decision.confidence,
                decision.thesis_hash, decision.inputs_hash, decision.model_used,
                decision.compute_source, decision_version, decision.decided_at.isoformat(),
            ),
        )
    return eid


def save_rating(rating: PositionRating) -> str:
    init_decision_schema()
    eid = rating_event_id(rating.ticker, rating.inputs_hash, rating.rating_version)
    with connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "rating", rating.ticker, rating.decided_at.isoformat(),
             json.dumps({
                 "action": rating.action,
                 "grade": rating.grade,
                 "risk_score": rating.risk_score,
                 "technical_state": rating.technical_state,
                 "compute_source": rating.compute_source,
             })),
        )
        conn.execute(
            """INSERT OR REPLACE INTO position_ratings
               (event_id, ticker, action, grade, attention_state, risk_score,
                risk_components, latest_close, ema20, price_vs_ema20_pct,
                technical_state, deterministic_grade, llm_grade, final_grade,
                note, drivers, confidence, thesis_hash, inputs_hash, model_used,
                compute_source, rating_version, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, rating.ticker, rating.action, rating.grade, rating.attention_state,
                rating.risk_score, json.dumps(rating.risk_components), rating.latest_close,
                rating.ema20, rating.price_vs_ema20_pct, rating.technical_state,
                rating.deterministic_grade, rating.llm_grade, rating.final_grade,
                rating.note, json.dumps(rating.drivers), rating.confidence,
                rating.thesis_hash, rating.inputs_hash, rating.model_used,
                rating.compute_source, rating.rating_version, rating.decided_at.isoformat(),
            ),
        )
    return eid


# True when a decision already exists for this exact (ticker, inputs_hash,
# decision_version). The staleness check run_decisions uses to skip holdings
# whose thesis and evidence haven't changed since the last compute.
def has_decision_for(ticker: str, inputs_hash: str, decision_version: str) -> bool:
    init_decision_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM position_decisions "
            "WHERE ticker = ? AND inputs_hash = ? AND decision_version = ? LIMIT 1",
            (ticker.upper(), inputs_hash, decision_version),
        ).fetchone()
    return row is not None


def has_rating_for(ticker: str, inputs_hash: str, rating_version: str) -> bool:
    init_decision_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM position_ratings "
            "WHERE ticker = ? AND inputs_hash = ? AND rating_version = ? LIMIT 1",
            (ticker.upper(), inputs_hash, rating_version),
        ).fetchone()
    return row is not None


# Latest decision for one ticker (most recent decided_at), or None.
def latest_decision(ticker: str):
    init_decision_schema()
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM position_decisions WHERE ticker = ? "
            "ORDER BY decided_at DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()


def latest_rating(ticker: str):
    init_decision_schema()
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM position_ratings WHERE ticker = ? "
            "ORDER BY decided_at DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()


# Latest decision per ticker — the dashboard/email feed. Ordered most-severe
# first (sell → watch → hold) then by ticker so the riskiest names lead.
def latest_decisions():
    init_decision_schema()
    sql = """
        SELECT d.* FROM position_decisions d
        JOIN (
            SELECT ticker, MAX(decided_at) AS max_at
            FROM position_decisions GROUP BY ticker
        ) latest
          ON d.ticker = latest.ticker AND d.decided_at = latest.max_at
        ORDER BY CASE d.verdict WHEN 'sell' THEN 0 WHEN 'watch' THEN 1 ELSE 2 END,
                 d.ticker
    """
    with connection() as conn:
        return conn.execute(sql).fetchall()


def latest_ratings():
    init_decision_schema()
    sql = """
        SELECT r.* FROM position_ratings r
        JOIN (
            SELECT ticker, MAX(decided_at) AS max_at
            FROM position_ratings GROUP BY ticker
        ) latest
          ON r.ticker = latest.ticker AND r.decided_at = latest.max_at
        ORDER BY CASE r.grade WHEN 'D' THEN 0 WHEN 'C' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
                 r.ticker
    """
    with connection() as conn:
        return conn.execute(sql).fetchall()

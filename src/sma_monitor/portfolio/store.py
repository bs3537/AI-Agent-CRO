"""SQLite persistence for Phase 1.

position_pulls — one row per Flex pull (provenance + raw XML for replay).
positions      — one row per (pull_id, ticker), the normalized record.

Both register their event_id into the universal events table from Phase 0,
so Phase 6 idempotency checks work across artifact kinds.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime

from ..db import connection
from ..identity import event_id
from .schema import Position

# DDL for the Phase 1 tables. position_pulls keeps the raw Flex XML so a
# pull can be replayed offline; positions is the normalized per-ticker view
# referenced by every downstream phase via ticker.
POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_pulls (
    pull_id     TEXT PRIMARY KEY,
    pulled_at   TEXT NOT NULL,
    nav         REAL NOT NULL,
    source      TEXT NOT NULL,
    raw_xml     TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    event_id      TEXT PRIMARY KEY,
    pull_id       TEXT NOT NULL REFERENCES position_pulls(pull_id),
    ticker        TEXT NOT NULL,
    qty           REAL NOT NULL,
    market_value  REAL NOT NULL,
    pct_nav       REAL NOT NULL,
    cost_basis    REAL,
    pulled_at     TEXT NOT NULL,
    nav           REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_pull_id   ON positions(pull_id);
CREATE INDEX IF NOT EXISTS idx_positions_ticker    ON positions(ticker);
CREATE INDEX IF NOT EXISTS idx_positions_pulled_at ON positions(pulled_at);
"""


# Create the Phase 1 tables. Safe to call repeatedly (CREATE TABLE IF NOT EXISTS).
def init_portfolio_schema() -> None:
    with connection() as conn:
        conn.executescript(POSITIONS_SCHEMA)


# Stable id for a Flex pull. Keyed on (pulled_at, nav) so re-running the
# same pull does not produce duplicate rows.
def pull_event_id(pulled_at: datetime, nav: float) -> str:
    return event_id({"kind": "position_pull", "pulled_at": pulled_at.isoformat(), "nav": nav})


# Stable id for one position row within a pull. Keyed on (pull_id, ticker).
def position_event_id(pull_id: str, ticker: str) -> str:
    return event_id({"kind": "position", "pull_id": pull_id, "ticker": ticker})


# Persist a Flex pull and all its position rows in a single transaction.
# Idempotent on (pulled_at, nav) — replaying the same pull is a no-op.
def save_pull(
    positions: Iterable[Position],
    *,
    nav: float,
    pulled_at: datetime,
    source: str,
    raw_xml: str | None,
) -> str:
    """Persist a Flex pull + its positions. Idempotent on (pulled_at, nav)."""
    init_portfolio_schema()
    pid = pull_event_id(pulled_at, nav)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                pid,
                "position_pull",
                None,
                pulled_at.isoformat(),
                json.dumps({"nav": nav, "source": source}),
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO position_pulls(pull_id, pulled_at, nav, source, raw_xml) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, pulled_at.isoformat(), nav, source, raw_xml),
        )
        for p in positions:
            eid = position_event_id(pid, p.ticker)
            conn.execute(
                "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    eid,
                    "position",
                    p.ticker,
                    p.pulled_at.isoformat(),
                    json.dumps(p.model_dump(mode="json")),
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO positions"
                "(event_id, pull_id, ticker, qty, market_value, pct_nav, cost_basis, pulled_at, nav) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    eid,
                    pid,
                    p.ticker,
                    p.qty,
                    p.market_value,
                    p.pct_nav,
                    p.cost_basis,
                    p.pulled_at.isoformat(),
                    p.nav,
                ),
            )
    return pid


# Read the most-recent pull's positions sorted by %NAV desc. Every
# downstream phase calls this (directly or via joined.latest_joined).
def latest_positions() -> tuple[list[Position], datetime | None]:
    """Read the most-recent pull's positions, sorted by %NAV desc."""
    init_portfolio_schema()
    with connection() as conn:
        head = conn.execute(
            "SELECT pull_id, pulled_at FROM position_pulls ORDER BY pulled_at DESC LIMIT 1"
        ).fetchone()
        if head is None:
            return [], None
        rows = conn.execute(
            "SELECT * FROM positions WHERE pull_id = ? ORDER BY pct_nav DESC",
            (head["pull_id"],),
        ).fetchall()
    positions = [
        Position(
            ticker=r["ticker"],
            qty=r["qty"],
            market_value=r["market_value"],
            pct_nav=r["pct_nav"],
            cost_basis=r["cost_basis"],
            pulled_at=datetime.fromisoformat(r["pulled_at"]),
            nav=r["nav"],
        )
        for r in rows
    ]
    return positions, datetime.fromisoformat(head["pulled_at"])

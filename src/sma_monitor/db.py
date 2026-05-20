import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .paths import DB_PATH

# Universal idempotency table. Every artifact across all phases registers
# its event_id here on creation. Per-phase tables (articles, scores, alerts,
# feedback) will be added in their respective phases and reference event_id.
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    ticker      TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    payload     TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_kind       ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_ticker     ON events(ticker);
CREATE INDEX IF NOT EXISTS idx_events_first_seen ON events(first_seen);
"""


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from .config import settings
from .paths import DB_PATH

# How long a connection waits on a locked DB before raising "database is
# locked" (W9: the bounded-concurrency LLM loops write results + cost-ledger
# rows from several threads at once). Overridable via env. WAL already lets
# readers and a single writer coexist; busy_timeout serializes the writers.
BUSY_TIMEOUT_MS = int(os.environ.get("SMA_SQLITE_BUSY_TIMEOUT_MS", "30000"))
_TURSO_LOCK = threading.RLock()

# Persistent singleton Turso connection. Re-establishing a libsql connection
# to Turso requires a full TLS+auth handshake on every call (~2-5s RTT), which
# makes multi-query request handlers unacceptably slow. We keep one connection
# alive for the process lifetime, reconnecting only on failure. All Turso
# operations already serialize under _TURSO_LOCK so there is no concurrency
# risk from sharing a single connection.
_TURSO_SINGLETON: Any = None

# Universal idempotency table. Every artifact across all phases registers
# its event_id here on creation. Per-phase tables (articles, scores,
# alerts, feedback, etc.) are defined in each phase's store.py and
# reference back into this table via event_id.
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


# Context manager that yields a DB-API connection. For Turso, a persistent
# singleton is returned (never closed between calls) to avoid repeated TLS+auth
# handshakes. The singleton is reconnected automatically on any error. For the
# test-only SQLite path, a fresh connection is opened and closed per call.
@contextmanager
def connection() -> Iterator[Any]:
    use_turso = _use_turso()
    lock = _TURSO_LOCK if use_turso else _NullLock()
    with lock:
        if use_turso:
            conn = _turso_singleton()
            try:
                yield conn
            except Exception:
                # On any DB error reset the singleton so the next call reconnects.
                _reset_turso_singleton()
                raise
        else:
            conn = _connect(use_turso=False)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()


# Return the persistent Turso singleton, creating it on first call.
def _turso_singleton() -> Any:
    global _TURSO_SINGLETON
    if _TURSO_SINGLETON is None:
        _TURSO_SINGLETON = _connect_turso()
    return _TURSO_SINGLETON


# Discard the singleton so the next call to _turso_singleton() reconnects.
def _reset_turso_singleton() -> None:
    global _TURSO_SINGLETON
    try:
        if _TURSO_SINGLETON is not None:
            _TURSO_SINGLETON._conn.close()
    except Exception:
        pass
    _TURSO_SINGLETON = None


def _connect(*, use_turso: bool | None = None) -> Any:
    use_turso = _use_turso() if use_turso is None else use_turso
    if use_turso:
        conn = _connect_turso()
        _configure_connection(conn, use_turso=True)
        return conn
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    _configure_connection(conn, use_turso=False)
    return conn


def _use_turso() -> bool:
    return not _use_test_sqlite()


def _use_test_sqlite() -> bool:
    return os.environ.get("SMA_TEST_SQLITE", "").strip() == "1"


def database_backend() -> str:
    return "turso" if _use_turso() else "sqlite-test"


def _connect_turso() -> Any:
    if not settings.turso_database_url or not settings.turso_auth_token:
        raise RuntimeError(
            "Turso is the only runtime DB backend; set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN"
        )
    try:
        import libsql
    except ImportError as e:
        raise RuntimeError("Turso support needs `libsql` installed") from e
    conn = libsql.connect(
        database=settings.turso_database_url,
        auth_token=settings.turso_auth_token,
    )
    return _TursoConnection(conn)


def _configure_connection(conn: Any, *, use_turso: bool) -> None:
    if not use_turso:
        conn.execute("PRAGMA journal_mode = WAL")
    for pragma in (
        "PRAGMA foreign_keys = ON",
        f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}",
    ):
        with suppress(Exception):
            conn.execute(pragma)


class _NamedRow(tuple):
    """Small sqlite3.Row-compatible mapping layer for libSQL tuple rows."""

    def __new__(cls, values: tuple[Any, ...], columns: tuple[str, ...]) -> "_NamedRow":
        obj = super().__new__(cls, values)
        obj._columns = columns
        obj._index = {column: idx for idx, column in enumerate(columns)}
        lower_index: dict[str, int] = {}
        for idx, column in enumerate(columns):
            lower_index.setdefault(column.lower(), idx)
        obj._lower_index = lower_index
        return obj

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            idx = self._index.get(key)
            if idx is None:
                idx = self._lower_index[key.lower()]
            return super().__getitem__(idx)
        return super().__getitem__(key)

    def keys(self) -> list[str]:
        return list(self._columns)

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._index and key.lower() not in self._lower_index:
            return default
        return self[key]


class _TursoCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def execute(self, *args: Any, **kwargs: Any) -> "_TursoCursor":
        self._cursor.execute(*args, **kwargs)
        return self

    def executemany(self, *args: Any, **kwargs: Any) -> "_TursoCursor":
        self._cursor.executemany(*args, **kwargs)
        return self

    def executescript(self, *args: Any, **kwargs: Any) -> "_TursoCursor":
        self._cursor.executescript(*args, **kwargs)
        return self

    def fetchone(self) -> _NamedRow | None:
        row = self._cursor.fetchone()
        if row is None:
            return None
        return self._wrap_row(row)

    def fetchall(self) -> list[_NamedRow]:
        return [self._wrap_row(row) for row in self._cursor.fetchall()]

    def fetchmany(self, size: int | None = None) -> list[_NamedRow]:
        rows = self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()
        return [self._wrap_row(row) for row in rows]

    def _wrap_row(self, row: Any) -> _NamedRow:
        columns = tuple(col[0] for col in (self._cursor.description or ()))
        return _NamedRow(tuple(row), columns)


class _TursoConnection:
    def __init__(self, conn: Any):
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def execute(self, *args: Any, **kwargs: Any) -> _TursoCursor:
        return _TursoCursor(self._conn.execute(*args, **kwargs))

    def executemany(self, *args: Any, **kwargs: Any) -> _TursoCursor:
        return _TursoCursor(self._conn.executemany(*args, **kwargs))

    def executescript(self, *args: Any, **kwargs: Any) -> _TursoCursor:
        return _TursoCursor(self._conn.executescript(*args, **kwargs))


class _NullLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: Any) -> None:
        return None


# Create the universal events table. Per-phase init_*_schema() functions
# are called alongside this one by the bootstrap and each CLI's main().
def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)

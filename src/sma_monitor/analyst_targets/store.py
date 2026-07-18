"""Current sell-side analyst target state persisted for dashboard reads."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..db import connection, init_db
from ..identity import event_id
from .tipranks import tipranks_forecast_url

# Target rows retain the last successful value while attempt status records freshness.
ANALYST_TARGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyst_price_targets (
    ticker                  TEXT PRIMARY KEY,
    event_id                TEXT NOT NULL UNIQUE,
    source                  TEXT NOT NULL,
    status                  TEXT NOT NULL,
    mean_price_target       REAL,
    high_price_target       REAL,
    low_price_target        REAL,
    analyst_count           INTEGER,
    currency                TEXT,
    source_url              TEXT NOT NULL,
    target_window           TEXT NOT NULL,
    target_fetched_at       TEXT,
    last_attempt_at         TEXT NOT NULL,
    unavailable_reason      TEXT,
    reference_close         REAL,
    price_as_of             TEXT,
    upside_pct              REAL,
    upside_updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_analyst_target_status
    ON analyst_price_targets(status);
CREATE INDEX IF NOT EXISTS idx_analyst_target_fetched_at
    ON analyst_price_targets(target_fetched_at);
"""

# Public presentation policy for source-specific target refreshes.
TIPRANKS_SOURCE = "tipranks"
TIPRANKS_WINDOW = "12_month_targets_issued_last_3_months"
FMP_SOURCE = "fmp"
FMP_WINDOW = "current_sell_side_consensus"
TARGET_STALE_AFTER = {
    TIPRANKS_SOURCE: timedelta(days=8),
    FMP_SOURCE: timedelta(days=4),
}
TARGET_FIELDS = (
    "ticker",
    "source",
    "status",
    "mean_price_target",
    "high_price_target",
    "low_price_target",
    "analyst_count",
    "currency",
    "source_url",
    "target_window",
    "target_fetched_at",
    "last_attempt_at",
    "unavailable_reason",
    "reference_close",
    "price_as_of",
    "upside_pct",
    "upside_updated_at",
)


class AnalystConsensusTarget(Protocol):
    mean_price_target: float
    high_price_target: float | None
    low_price_target: float | None
    analyst_count: int | None
    currency: str | None
    source_url: str


# Create the current-state table and its universal events dependency.
def init_analyst_target_schema() -> None:
    init_db()
    with connection() as conn:
        conn.executescript(ANALYST_TARGET_SCHEMA)


# Build the stable artifact id used for the current target state of one ticker.
def analyst_target_event_id(ticker: str) -> str:
    return event_id({"kind": "analyst_price_target", "ticker": ticker.upper()})


# Persist a successful analyst target and clear any previous failure status.
def save_target_success(
    ticker: str,
    target: AnalystConsensusTarget,
    *,
    fetched_at: datetime | None = None,
    source: str = TIPRANKS_SOURCE,
    target_window: str = TIPRANKS_WINDOW,
    source_url: str | None = None,
) -> str:
    init_analyst_target_schema()
    ticker = ticker.strip().upper()
    fetched_at = fetched_at or datetime.now(UTC)
    eid = analyst_target_event_id(ticker)
    payload = {
        "source": source,
        "status": "current",
        "mean_price_target": target.mean_price_target,
        "analyst_count": target.analyst_count,
    }
    with connection() as conn:
        _save_event(conn, eid, ticker, fetched_at, payload)
        conn.execute(
            """INSERT OR REPLACE INTO analyst_price_targets
               (ticker, event_id, source, status, mean_price_target,
                high_price_target, low_price_target, analyst_count, currency,
                source_url, target_window, target_fetched_at, last_attempt_at,
                unavailable_reason, reference_close, price_as_of, upside_pct,
                upside_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker,
                eid,
                source,
                "current",
                target.mean_price_target,
                target.high_price_target,
                target.low_price_target,
                target.analyst_count,
                target.currency,
                source_url or target.source_url,
                target_window,
                fetched_at.isoformat(),
                fetched_at.isoformat(),
                None,
                None,
                None,
                None,
                None,
            ),
        )
    return eid


# Persist an explicit no-coverage result and clear any obsolete target.
def save_target_unavailable(
    ticker: str,
    *,
    attempted_at: datetime | None = None,
    reason: str = "no_analyst_coverage",
    source: str = TIPRANKS_SOURCE,
    target_window: str = TIPRANKS_WINDOW,
    source_url: str | None = None,
) -> str:
    init_analyst_target_schema()
    ticker = ticker.strip().upper()
    attempted_at = attempted_at or datetime.now(UTC)
    eid = analyst_target_event_id(ticker)
    source_url = source_url or tipranks_forecast_url(ticker)
    payload = {"source": source, "status": "unavailable", "reason": reason}
    with connection() as conn:
        _save_event(conn, eid, ticker, attempted_at, payload)
        conn.execute(
            """INSERT OR REPLACE INTO analyst_price_targets
               (ticker, event_id, source, status, mean_price_target,
                high_price_target, low_price_target, analyst_count, currency,
                source_url, target_window, target_fetched_at, last_attempt_at,
                unavailable_reason, reference_close, price_as_of, upside_pct,
                upside_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker,
                eid,
                source,
                "unavailable",
                None,
                None,
                None,
                None,
                None,
                source_url,
                target_window,
                None,
                attempted_at.isoformat(),
                reason,
                None,
                None,
                None,
                None,
            ),
        )
    return eid


# Record a scrape failure while preserving any last successful target as stale.
def mark_target_failure(
    ticker: str,
    *,
    attempted_at: datetime | None = None,
    reason: str = "scrape_failed",
    source: str = TIPRANKS_SOURCE,
    target_window: str = TIPRANKS_WINDOW,
    source_url: str | None = None,
) -> str:
    init_analyst_target_schema()
    ticker = ticker.strip().upper()
    attempted_at = attempted_at or datetime.now(UTC)
    eid = analyst_target_event_id(ticker)
    with connection() as conn:
        row = conn.execute(
            "SELECT source, mean_price_target FROM analyst_price_targets WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        if row is None:
            _save_event(
                conn,
                eid,
                ticker,
                attempted_at,
                {"source": source, "status": "unavailable", "reason": reason},
            )
            conn.execute(
                """INSERT INTO analyst_price_targets
                   (ticker, event_id, source, status, source_url, target_window,
                    last_attempt_at, unavailable_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker,
                    eid,
                    source,
                    "unavailable",
                    source_url or tipranks_forecast_url(ticker),
                    target_window,
                    attempted_at.isoformat(),
                    reason,
                ),
            )
        else:
            status = "stale" if row["mean_price_target"] is not None else "unavailable"
            _save_event(
                conn,
                eid,
                ticker,
                attempted_at,
                {"source": row["source"], "status": status, "reason": reason},
            )
            conn.execute(
                """UPDATE analyst_price_targets
                   SET status = ?, last_attempt_at = ?, unavailable_reason = ?
                   WHERE ticker = ?""",
                (status, attempted_at.isoformat(), reason, ticker),
            )
    return eid


# Persist an ETF as not applicable and clear any obsolete analyst target/upside.
def save_target_not_applicable(
    ticker: str,
    *,
    attempted_at: datetime | None = None,
    source: str = FMP_SOURCE,
    target_window: str = FMP_WINDOW,
    source_url: str | None = None,
) -> str:
    init_analyst_target_schema()
    ticker = ticker.strip().upper()
    attempted_at = attempted_at or datetime.now(UTC)
    eid = analyst_target_event_id(ticker)
    source_url = source_url or tipranks_forecast_url(ticker)
    with connection() as conn:
        _save_event(
            conn,
            eid,
            ticker,
            attempted_at,
            {"source": source, "status": "not_applicable", "reason": "etf"},
        )
        conn.execute(
            """INSERT OR REPLACE INTO analyst_price_targets
               (ticker, event_id, source, status, mean_price_target,
                high_price_target, low_price_target, analyst_count, currency,
                source_url, target_window, target_fetched_at, last_attempt_at,
                unavailable_reason, reference_close, price_as_of, upside_pct,
                upside_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker,
                eid,
                source,
                "not_applicable",
                None,
                None,
                None,
                None,
                None,
                source_url,
                target_window,
                None,
                attempted_at.isoformat(),
                "etf",
                None,
                None,
                None,
                None,
            ),
        )
    return eid


# Attach a dated EOD reference close and recomputed upside to a saved target.
def apply_reference_price(
    ticker: str,
    *,
    reference_close: float,
    price_as_of: str,
    updated_at: datetime | None = None,
) -> bool:
    init_analyst_target_schema()
    ticker = ticker.strip().upper()
    updated_at = updated_at or datetime.now(UTC)
    if reference_close <= 0:
        return False
    with connection() as conn:
        row = conn.execute(
            """SELECT mean_price_target, reference_close, price_as_of
               FROM analyst_price_targets WHERE ticker = ?""",
            (ticker,),
        ).fetchone()
        if row is None or row["mean_price_target"] is None:
            return False
        if (
            row["price_as_of"] == price_as_of
            and row["reference_close"] is not None
            and abs(float(row["reference_close"]) - reference_close) < 1e-9
        ):
            return False
        upside_pct = float(row["mean_price_target"]) / reference_close - 1.0
        conn.execute(
            """UPDATE analyst_price_targets
               SET reference_close = ?, price_as_of = ?, upside_pct = ?,
                   upside_updated_at = ?
               WHERE ticker = ?""",
            (
                reference_close,
                price_as_of,
                upside_pct,
                updated_at.isoformat(),
                ticker,
            ),
        )
    return True


# Return the current dashboard-facing target state for one ticker.
def latest_target_state(
    ticker: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    init_analyst_target_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM analyst_price_targets WHERE ticker = ?",
            (ticker.strip().upper(),),
        ).fetchone()
    return target_state_from_row(row, now=now)


# Convert a SQLite/Turso row to the public state while enforcing age-based staleness.
def target_state_from_row(
    row: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    state = {field: row[field] for field in TARGET_FIELDS}
    now = now or datetime.now(UTC)
    fetched_at = _parse_datetime(state["target_fetched_at"])
    if (
        state["status"] == "current"
        and fetched_at is not None
        and now - fetched_at
        > TARGET_STALE_AFTER.get(state["source"], TARGET_STALE_AFTER[FMP_SOURCE])
    ):
        state["status"] = "stale"
        state["unavailable_reason"] = "target_refresh_overdue"
    return state


# Insert or replace the universal event row for the ticker's current target state.
def _save_event(
    conn: Any,
    eid: str,
    ticker: str,
    changed_at: datetime,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO events(event_id, kind, ticker, first_seen, payload)
           VALUES (?, ?, ?, ?, ?)""",
        (
            eid,
            "analyst_price_target",
            ticker,
            changed_at.isoformat(),
            json.dumps(payload),
        ),
    )


# Parse stored ISO datetimes and normalize naive legacy values to UTC.
def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

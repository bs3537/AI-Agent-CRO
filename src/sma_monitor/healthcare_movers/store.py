"""Durable universe, EOD price, and ranking snapshots for healthcare movers."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ..db import connection
from ..identity import event_id

PRICE_WRITE_CHUNK_SIZE = 100

HEALTHCARE_MOVERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS healthcare_mover_universe (
    ticker          TEXT PRIMARY KEY,
    company_name    TEXT NOT NULL,
    sector          TEXT NOT NULL,
    industry        TEXT,
    exchange        TEXT NOT NULL,
    country         TEXT NOT NULL,
    market_cap      REAL,
    latest_price    REAL,
    latest_volume   INTEGER,
    is_active       INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    source          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hm_universe_active
    ON healthcare_mover_universe(is_active, ticker);

CREATE TABLE IF NOT EXISTS healthcare_mover_prices (
    ticker           TEXT NOT NULL,
    price_date       TEXT NOT NULL,
    close            REAL NOT NULL,
    volume           INTEGER,
    source_timestamp INTEGER,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (ticker, price_date)
);
CREATE INDEX IF NOT EXISTS idx_hm_prices_date
    ON healthcare_mover_prices(price_date, ticker);

CREATE TABLE IF NOT EXISTS healthcare_mover_runs (
    run_id            TEXT PRIMARY KEY,
    as_of_date        TEXT,
    status            TEXT NOT NULL,
    universe_count    INTEGER NOT NULL,
    covered_count     INTEGER NOT NULL,
    coverage_fraction REAL NOT NULL,
    generated_at      TEXT NOT NULL,
    error_summary     TEXT
);
CREATE INDEX IF NOT EXISTS idx_hm_runs_generated
    ON healthcare_mover_runs(generated_at);

CREATE TABLE IF NOT EXISTS healthcare_mover_rankings (
    run_id              TEXT NOT NULL,
    window_days         INTEGER NOT NULL,
    direction           TEXT NOT NULL,
    rank                INTEGER NOT NULL,
    ticker              TEXT NOT NULL,
    company_name        TEXT NOT NULL,
    industry            TEXT,
    exchange            TEXT,
    market_cap          REAL,
    price               REAL NOT NULL,
    return_pct          REAL NOT NULL,
    start_date          TEXT NOT NULL,
    end_date            TEXT NOT NULL,
    start_close         REAL NOT NULL,
    end_close           REAL NOT NULL,
    latest_volume       INTEGER,
    average_volume_20d  REAL,
    volume_ratio        REAL,
    flags               TEXT NOT NULL,
    spark_dates         TEXT NOT NULL,
    spark_closes        TEXT NOT NULL,
    PRIMARY KEY (run_id, window_days, direction, rank)
);
CREATE INDEX IF NOT EXISTS idx_hm_rankings_run
    ON healthcare_mover_rankings(run_id, window_days, direction, rank);
"""


def init_healthcare_movers_schema() -> None:
    with connection() as conn:
        conn.executescript(HEALTHCARE_MOVERS_SCHEMA)


def save_universe_snapshot(
    rows: Sequence[Mapping[str, Any]],
    *,
    observed_at: datetime | None = None,
    source: str = "fmp_company_screener",
) -> dict[str, int]:
    """Upsert one complete universe snapshot and deactivate absent symbols."""
    init_healthcare_movers_schema()
    observed_at = observed_at or datetime.now(UTC)
    timestamp = observed_at.isoformat()
    normalized = {
        str(row["ticker"]).strip().upper(): row
        for row in rows
        if row.get("ticker")
    }
    with connection() as conn:
        existing = {
            row["ticker"]
            for row in conn.execute(
                "SELECT ticker FROM healthcare_mover_universe WHERE is_active = 1"
            ).fetchall()
        }
        for ticker, row in normalized.items():
            conn.execute(
                """INSERT INTO healthcare_mover_universe (
                       ticker, company_name, sector, industry, exchange, country,
                       market_cap, latest_price, latest_volume, is_active,
                       first_seen_at, last_seen_at, source
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                       company_name = excluded.company_name,
                       sector = excluded.sector,
                       industry = excluded.industry,
                       exchange = excluded.exchange,
                       country = excluded.country,
                       market_cap = excluded.market_cap,
                       latest_price = excluded.latest_price,
                       latest_volume = excluded.latest_volume,
                       is_active = 1,
                       last_seen_at = excluded.last_seen_at,
                       source = excluded.source""",
                (
                    ticker,
                    str(row.get("company_name") or ticker),
                    str(row.get("sector") or "Healthcare"),
                    row.get("industry"),
                    str(row.get("exchange") or ""),
                    str(row.get("country") or "US"),
                    row.get("market_cap"),
                    row.get("latest_price"),
                    row.get("latest_volume"),
                    timestamp,
                    timestamp,
                    source,
                ),
            )
        removed = existing - set(normalized)
        if removed:
            placeholders = ",".join("?" for _ in removed)
            conn.execute(
                f"""UPDATE healthcare_mover_universe
                    SET is_active = 0, last_seen_at = ?
                    WHERE ticker IN ({placeholders})""",
                (timestamp, *sorted(removed)),
            )
    return {
        "active": len(normalized),
        "added": len(set(normalized) - existing),
        "removed": len(removed),
    }


def active_universe() -> list[dict[str, Any]]:
    init_healthcare_movers_schema()
    with connection() as conn:
        rows = conn.execute(
            """SELECT ticker, company_name, sector, industry, exchange, country,
                      market_cap, latest_price, latest_volume
               FROM healthcare_mover_universe
               WHERE is_active = 1
               ORDER BY ticker"""
        ).fetchall()
    return [dict(row) for row in rows]


def save_price_points(
    rows: Sequence[Mapping[str, Any]],
    *,
    fetched_at: datetime | None = None,
) -> int:
    init_healthcare_movers_schema()
    fetched_at = fetched_at or datetime.now(UTC)
    values: list[tuple[Any, ...]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        price_date = str(row.get("price_date") or "").strip()
        try:
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not ticker or not price_date or close <= 0:
            continue
        volume = row.get("volume")
        values.append(
            (
                ticker,
                price_date,
                close,
                int(volume) if volume is not None else None,
                row.get("source_timestamp"),
                fetched_at.isoformat(),
            )
        )
    if not values:
        return 0
    with connection() as conn:
        for offset in range(0, len(values), PRICE_WRITE_CHUNK_SIZE):
            chunk = values[offset : offset + PRICE_WRITE_CHUNK_SIZE]
            placeholders = ",".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
            parameters = tuple(value for row in chunk for value in row)
            conn.execute(
                f"""INSERT INTO healthcare_mover_prices (
                        ticker, price_date, close, volume, source_timestamp, fetched_at
                    ) VALUES {placeholders}
                    ON CONFLICT(ticker, price_date) DO UPDATE SET
                        close = excluded.close,
                        volume = excluded.volume,
                        source_timestamp = excluded.source_timestamp,
                        fetched_at = excluded.fetched_at""",
                parameters,
            )
    return len(values)


def price_histories(
    tickers: Sequence[str],
    *,
    max_sessions: int = 30,
) -> dict[str, list[dict[str, Any]]]:
    init_healthcare_movers_schema()
    normalized = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    result = {ticker: [] for ticker in normalized}
    if not normalized:
        return result
    placeholders = ",".join("?" for _ in normalized)
    with connection() as conn:
        rows = conn.execute(
            f"""SELECT ticker, price_date, close, volume, source_timestamp
                FROM healthcare_mover_prices
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, price_date DESC""",
            normalized,
        ).fetchall()
    for row in rows:
        ticker = row["ticker"]
        if len(result[ticker]) >= max_sessions:
            continue
        result[ticker].append(dict(row))
    for history in result.values():
        history.reverse()
    return result


def save_ranking_run(
    result: Mapping[str, Any],
    *,
    status: str = "current",
    generated_at: datetime | None = None,
    error_summary: str | None = None,
) -> str:
    """Persist a complete immutable ranking snapshot in one transaction."""
    init_healthcare_movers_schema()
    generated_at = generated_at or datetime.now(UTC)
    as_of_date = result.get("as_of_date")
    universe_count = int(result.get("universe_count") or 0)
    covered = int((result.get("covered_by_window") or {}).get("5") or 0)
    coverage = covered / universe_count if universe_count else 0.0
    run_id = event_id(
        {
            "kind": "healthcare_mover_run",
            "as_of_date": as_of_date,
            "generated_at": generated_at.isoformat(),
        }
    )
    with connection() as conn:
        conn.execute(
            """INSERT INTO healthcare_mover_runs (
                   run_id, as_of_date, status, universe_count, covered_count,
                   coverage_fraction, generated_at, error_summary
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                as_of_date,
                status,
                universe_count,
                covered,
                coverage,
                generated_at.isoformat(),
                error_summary,
            ),
        )
        for window, directions in (result.get("rankings") or {}).items():
            for direction, rows in directions.items():
                for row in rows:
                    conn.execute(
                        """INSERT INTO healthcare_mover_rankings (
                               run_id, window_days, direction, rank, ticker,
                               company_name, industry, exchange, market_cap, price,
                               return_pct, start_date, end_date, start_close, end_close,
                               latest_volume, average_volume_20d, volume_ratio,
                               flags, spark_dates, spark_closes
                           ) VALUES (
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                           )""",
                        (
                            run_id,
                            int(window),
                            direction,
                            row["rank"],
                            row["ticker"],
                            row["company_name"],
                            row.get("industry"),
                            row.get("exchange"),
                            row.get("market_cap"),
                            row["price"],
                            row["return_pct"],
                            row["start_date"],
                            row["end_date"],
                            row["start_close"],
                            row["end_close"],
                            row.get("latest_volume"),
                            row.get("average_volume_20d"),
                            row.get("volume_ratio"),
                            json.dumps(row.get("flags") or []),
                            json.dumps(row.get("spark_dates") or []),
                            json.dumps(row.get("spark_closes") or []),
                        ),
                    )
    return run_id


def latest_ranking_snapshot() -> dict[str, Any] | None:
    init_healthcare_movers_schema()
    with connection() as conn:
        run = conn.execute(
            """SELECT run_id, as_of_date, status, universe_count, covered_count,
                      coverage_fraction, generated_at, error_summary
               FROM healthcare_mover_runs
               WHERE status IN ('current', 'partial')
               ORDER BY generated_at DESC LIMIT 1"""
        ).fetchone()
        if run is None:
            return None
        rows = conn.execute(
            """SELECT * FROM healthcare_mover_rankings
               WHERE run_id = ?
               ORDER BY window_days, direction, rank""",
            (run["run_id"],),
        ).fetchall()
    rankings = {
        str(window): {"gainers": [], "decliners": []} for window in range(1, 6)
    }
    for raw in rows:
        row = dict(raw)
        row["flags"] = json.loads(row["flags"])
        row["spark_dates"] = json.loads(row["spark_dates"])
        row["spark_closes"] = json.loads(row["spark_closes"])
        row.pop("run_id", None)
        rankings[str(row["window_days"])][row["direction"]].append(row)
    return {
        "run_id": run["run_id"],
        "as_of_date": run["as_of_date"],
        "status": run["status"],
        "universe_count": run["universe_count"],
        "covered_count": run["covered_count"],
        "coverage_fraction": run["coverage_fraction"],
        "generated_at": run["generated_at"],
        "error_summary": run["error_summary"],
        "rankings": rankings,
    }

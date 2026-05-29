"""Financial Modeling Prep (FMP) adapter (W2 — financials for #4/#7/#12).

Fetches a compact per-ticker metrics dict (profile + TTM ratios + TTM key
metrics) and caches it in the fmp_snapshots table. The decision engine folds
the latest snapshot into its candidate (`fmp_metrics`), and the API serves it
as a position's `financials`. Fetching happens in the daily collect cycle, so
the decision/API reads are cheap DB lookups (and return None offline).

Auth: an `apikey` query param (FMP_API_KEY). Get one at
https://site.financialmodelingprep.com/ — set FMP_API_KEY in .env. Until then
refresh_for_holdings is skipped, so this is never called live without the key.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..db import connection
from ..identity import event_id

FMP_BASE = "https://financialmodelingprep.com/stable"

# Fields pulled from each FMP /stable endpoint into the flat metrics dict, mapped
# to friendly keys for the decision prompt + dashboard. Endpoint → {api_field: out_key}.
# The stable API (legacy /api/v3 retired Aug 31 2025) renamed several fields
# (mktCap→marketCap, peRatioTTM→priceToEarningsRatioTTM, debtEquityRatioTTM→
# debtToEquityRatioTTM) and moved cash/FCF-per-share from key-metrics-ttm into ratios-ttm.
_PROFILE_FIELDS = {"companyName": "company", "sector": "sector", "marketCap": "market_cap",
                   "price": "price", "beta": "beta"}
_RATIOS_FIELDS = {"priceToEarningsRatioTTM": "pe_ttm", "currentRatioTTM": "current_ratio",
                  "quickRatioTTM": "quick_ratio", "debtToEquityRatioTTM": "debt_to_equity",
                  "grossProfitMarginTTM": "gross_margin", "netProfitMarginTTM": "net_margin",
                  "cashPerShareTTM": "cash_per_share", "freeCashFlowPerShareTTM": "fcf_per_share"}
_KEYMETRICS_FIELDS = {"enterpriseValueTTM": "enterprise_value"}

# DDL for the per-ticker financial snapshot cache + the daily EOD price series
# that backs each position's sparkline. One row per (ticker, day) for each, so
# a same-day re-run is idempotent while history is retained.
FMP_SCHEMA = """
CREATE TABLE IF NOT EXISTS fmp_snapshots (
    event_id    TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    metrics     TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fmp_ticker     ON fmp_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_fmp_fetched_at ON fmp_snapshots(fetched_at);

CREATE TABLE IF NOT EXISTS price_series (
    event_id    TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    closes      TEXT NOT NULL,        -- json array of daily EOD closes, oldest→newest
    start_date  TEXT,
    end_date    TEXT,
    fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_ticker     ON price_series(ticker);
CREATE INDEX IF NOT EXISTS idx_price_fetched_at ON price_series(fetched_at);
"""

# ~1 trading year of daily closes for the sparkline.
PRICE_HISTORY_DAYS = 365


# Exception for any non-200 / unparseable FMP response.
class FmpError(RuntimeError):
    pass


# Create the fmp_snapshots table. Safe to call repeatedly.
def init_fmp_schema() -> None:
    with connection() as conn:
        conn.executescript(FMP_SCHEMA)


# Fetch a compact metrics dict for one ticker from FMP (profile + TTM ratios +
# TTM key metrics). Missing endpoints/fields are skipped, not fatal — returns
# whatever was retrieved.
def fetch_metrics(ticker: str, *, api_key: str, client: httpx.Client | None = None) -> dict:
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    metrics: dict[str, Any] = {}
    try:
        metrics.update(_pull(client, f"{FMP_BASE}/profile", ticker, api_key, _PROFILE_FIELDS))
        metrics.update(_pull(client, f"{FMP_BASE}/ratios-ttm", ticker, api_key, _RATIOS_FIELDS))
        metrics.update(_pull(client, f"{FMP_BASE}/key-metrics-ttm", ticker, api_key, _KEYMETRICS_FIELDS))
    finally:
        if owns:
            client.close()
    return metrics


# GET one FMP /stable endpoint for a symbol (each returns a single-element list)
# and project the selected fields into the friendly-keyed output. Tolerant of an
# empty list.
def _pull(client: httpx.Client, url: str, symbol: str, api_key: str, field_map: dict[str, str]) -> dict:
    resp = client.get(url, params={"symbol": symbol, "apikey": api_key})
    if resp.status_code != 200:
        raise FmpError(f"FMP {url} failed: {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    row = body[0] if isinstance(body, list) and body else (body if isinstance(body, dict) else {})
    return {out: row[api] for api, out in field_map.items() if row.get(api) is not None}


# Project the selected fields out of an already-parsed FMP fixture body (a dict
# keyed by endpoint name → list/dict). Used by load_metrics_file for offline replay.
def parse_metrics(body: dict[str, Any]) -> dict:
    metrics: dict[str, Any] = {}
    for key, fmap in (("profile", _PROFILE_FIELDS), ("ratios", _RATIOS_FIELDS),
                      ("key_metrics", _KEYMETRICS_FIELDS)):
        section = body.get(key)
        row = section[0] if isinstance(section, list) and section else (section or {})
        metrics.update({out: row[api] for api, out in fmap.items() if isinstance(row, dict) and row.get(api) is not None})
    return metrics


# Stable id for a snapshot — one per (ticker, UTC date) so same-day re-runs
# don't duplicate rows.
def fmp_event_id(ticker: str, fetched_at: datetime) -> str:
    return event_id({"kind": "fmp_snapshot", "ticker": ticker.upper(),
                     "date": fetched_at.date().isoformat()})


# Persist one ticker's metrics snapshot (idempotent per ticker per day) and
# register it in the events table.
def save_fmp_snapshot(ticker: str, metrics: dict, *, fetched_at: datetime | None = None) -> str:
    init_fmp_schema()
    fetched_at = fetched_at or datetime.now(timezone.utc)
    ticker = ticker.upper()
    eid = fmp_event_id(ticker, fetched_at)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "fmp_snapshot", ticker, fetched_at.isoformat(),
             json.dumps({"n_metrics": len(metrics)})),
        )
        conn.execute(
            "INSERT OR REPLACE INTO fmp_snapshots(event_id, ticker, metrics, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (eid, ticker, json.dumps(metrics), fetched_at.isoformat()),
        )
    return eid


# Return the most recent metrics dict for a ticker, or None when none cached
# (the offline / no-key state). Consumed by the decision engine + API.
def latest_fmp_metrics(ticker: str) -> dict | None:
    init_fmp_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT metrics FROM fmp_snapshots WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["metrics"])
    except (TypeError, json.JSONDecodeError):
        return None


# Refresh FMP snapshots for every held ticker. Reads from a fixture when
# from_file is set (a {ticker: metrics} map), else hits the live API. Raises
# RuntimeError when no key and no fixture — the collect cycle skips gracefully.
def refresh_for_holdings(
    *,
    api_key: str | None,
    from_file: Path | None = None,
    tickers: list[str] | None = None,
) -> dict:
    from ..portfolio.joined import latest_joined

    init_fmp_schema()
    if tickers is None:
        holdings, _missing, _ = latest_joined()
        tickers = [h.ticker for h in holdings]

    if from_file is not None:
        by_ticker = {t.upper(): m for t, m in json.loads(from_file.read_text()).items()}
        n = 0
        for t in tickers:
            metrics = by_ticker.get(t.upper())
            if metrics:
                save_fmp_snapshot(t, metrics)
                n += 1
        return {"tickers": len(tickers), "updated": n, "source": "fixture"}

    if not api_key:
        raise RuntimeError(
            "FMP_API_KEY is not set and no --from-file fixture was provided; "
            "financials refresh skipped."
        )

    updated = 0
    errors = 0
    with httpx.Client(timeout=30.0) as client:
        for t in tickers:
            try:
                metrics = fetch_metrics(t, api_key=api_key, client=client)
            except FmpError:
                errors += 1
                continue
            if metrics:
                save_fmp_snapshot(t, metrics)
                updated += 1
    # A total wipeout (every request errored) is a systemic failure — a dead
    # endpoint or revoked key — not "no new data". Raise so the caller flags it
    # loudly rather than silently caching nothing (this is what hid the retired
    # /api/v3 403s instead of tripping fmp_failure).
    if tickers and errors == len(tickers):
        raise FmpError(f"all {len(tickers)} FMP metrics requests failed (endpoint or key issue?)")
    return {"tickers": len(tickers), "updated": updated, "errors": errors, "source": "fmp"}


# Fetch ~1 year of daily EOD closes for one ticker (oldest→newest). Uses FMP's
# /stable/historical-price-eod/full endpoint, which returns a newest-first array
# of daily OHLC rows; we keep the closes and reverse them. Returns [] on any failure.
def fetch_price_history(
    ticker: str, *, api_key: str, days: int = PRICE_HISTORY_DAYS, client: httpx.Client | None = None
) -> list[float]:
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        today = datetime.now(timezone.utc).date()
        params = {"symbol": ticker, "apikey": api_key,
                  "from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()}
        resp = client.get(f"{FMP_BASE}/historical-price-eod/full", params=params)
        if resp.status_code != 200:
            raise FmpError(f"FMP history {ticker} failed: {resp.status_code} {resp.text[:200]}")
        return _parse_history(resp.json())
    finally:
        if owns:
            client.close()


# Parse an FMP price-history body into an oldest→newest list of closes. The
# /stable endpoint returns a flat array of daily rows; the legacy endpoint nested
# them under "historical". Accept either; FMP returns newest-first so we reverse.
def _parse_history(body: Any) -> list[float]:
    rows = body if isinstance(body, list) else (body.get("historical") or [])
    closes = [r["close"] for r in rows if isinstance(r, dict) and r.get("close") is not None]
    closes.reverse()  # FMP returns newest-first; sparkline wants oldest→newest
    return closes


# Stable id for a price series — one per (ticker, UTC date).
def price_event_id(ticker: str, fetched_at: datetime) -> str:
    return event_id({"kind": "price_series", "ticker": ticker.upper(),
                     "date": fetched_at.date().isoformat()})


# Persist one ticker's daily-close series (idempotent per ticker per day).
def save_price_series(ticker: str, closes: list[float], *, fetched_at: datetime | None = None) -> str:
    init_fmp_schema()
    fetched_at = fetched_at or datetime.now(timezone.utc)
    ticker = ticker.upper()
    eid = price_event_id(ticker, fetched_at)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "price_series", ticker, fetched_at.isoformat(),
             json.dumps({"n_points": len(closes)})),
        )
        conn.execute(
            "INSERT OR REPLACE INTO price_series(event_id, ticker, closes, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (eid, ticker, json.dumps(closes), fetched_at.isoformat()),
        )
    return eid


# Return the most recent daily-close series for a ticker, or None when none
# cached (offline / no key). Consumed by the API to render the sparkline.
def latest_price_series(ticker: str) -> list[float] | None:
    init_fmp_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT closes FROM price_series WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["closes"])
    except (TypeError, json.JSONDecodeError):
        return None


# Refresh daily-close series for every held ticker. Fixture (a {ticker: [closes]}
# or {ticker: [{date, close}]} map) when from_file is set, else the live API.
# Raises RuntimeError when no key and no fixture — collect skips gracefully.
def refresh_prices_for_holdings(
    *,
    api_key: str | None,
    from_file: Path | None = None,
    days: int = PRICE_HISTORY_DAYS,
    tickers: list[str] | None = None,
) -> dict:
    from ..portfolio.joined import latest_joined

    init_fmp_schema()
    if tickers is None:
        holdings, _missing, _ = latest_joined()
        tickers = [h.ticker for h in holdings]

    if from_file is not None:
        raw = {t.upper(): v for t, v in json.loads(from_file.read_text()).items()}
        n = 0
        for t in tickers:
            series = raw.get(t.upper())
            if not series:
                continue
            closes = [p["close"] if isinstance(p, dict) else float(p) for p in series]
            save_price_series(t, closes)
            n += 1
        return {"tickers": len(tickers), "updated": n, "source": "fixture"}

    if not api_key:
        raise RuntimeError(
            "FMP_API_KEY is not set and no --prices-file fixture was provided; "
            "price-history refresh skipped."
        )

    updated = 0
    errors = 0
    with httpx.Client(timeout=30.0) as client:
        for t in tickers:
            try:
                closes = fetch_price_history(t, api_key=api_key, days=days, client=client)
            except FmpError:
                errors += 1
                continue
            if closes:
                save_price_series(t, closes)
                updated += 1
    # Total wipeout → systemic failure; raise so collect trips fmp_failure.
    if tickers and errors == len(tickers):
        raise FmpError(f"all {len(tickers)} FMP price-history requests failed (endpoint or key issue?)")
    return {"tickers": len(tickers), "updated": updated, "errors": errors, "source": "fmp"}

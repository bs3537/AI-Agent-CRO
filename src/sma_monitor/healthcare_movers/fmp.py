"""FMP adapters for the US healthcare equity universe and EOD prices."""
from __future__ import annotations

import math
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..news.fmp_client import FMP_BASE, FmpError

EXCHANGES = ("NASDAQ", "NYSE", "AMEX", "OTC")
NEW_YORK = ZoneInfo("America/New_York")
QUOTE_CHUNK_SIZE = 100
NON_HEALTHCARE_INDUSTRIES = {
    "Asset Management - Cryptocurrency",
    "Financial - Credit Services",
    "Gold",
    "Hardware, Equipment & Parts",
    "Shell Companies",
}


def fetch_healthcare_universe(
    *,
    api_key: str,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Return active, non-fund US healthcare listings from FMP's screener."""
    owns_client = client is None
    client = client or httpx.Client(timeout=45.0)
    rows_by_ticker: dict[str, dict[str, Any]] = {}
    try:
        for exchange in EXCHANGES:
            body = _get_json(
                client,
                "company-screener",
                params={
                    "sector": "Healthcare",
                    "country": "US",
                    "exchange": exchange,
                    "isActivelyTrading": "true",
                    "isEtf": "false",
                    "isFund": "false",
                    "limit": 1000,
                    "page": 0,
                    "apikey": api_key,
                },
            )
            if not isinstance(body, list):
                raise FmpError(f"FMP company-screener {exchange} returned invalid data")
            for raw in body:
                row = _parse_universe_row(raw)
                if row is not None:
                    rows_by_ticker[row["ticker"]] = row
    finally:
        if owns_client:
            client.close()
    return [rows_by_ticker[ticker] for ticker in sorted(rows_by_ticker)]


def fetch_batch_quotes(
    tickers: Sequence[str],
    *,
    api_key: str,
    client: httpx.Client | None = None,
    chunk_size: int = QUOTE_CHUNK_SIZE,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch current quotes in FMP-supported chunks and map them to NY dates."""
    normalized = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not normalized:
        return []
    owns_client = client is None
    client = client or httpx.Client(timeout=45.0)
    fallback_date = (now or datetime.now(UTC)).astimezone(NEW_YORK).date().isoformat()
    points: list[dict[str, Any]] = []
    try:
        for offset in range(0, len(normalized), max(1, chunk_size)):
            chunk = normalized[offset : offset + max(1, chunk_size)]
            body = _get_json(
                client,
                "batch-quote",
                params={"symbols": ",".join(chunk), "apikey": api_key},
            )
            if not isinstance(body, list):
                raise FmpError("FMP batch-quote returned invalid data")
            for raw in body:
                if not isinstance(raw, dict):
                    continue
                ticker = str(raw.get("symbol") or "").strip().upper()
                close = _positive_float(raw.get("price"))
                if ticker not in chunk or close is None:
                    continue
                source_timestamp = _optional_int(raw.get("timestamp"))
                price_date = (
                    datetime.fromtimestamp(source_timestamp, UTC)
                    .astimezone(NEW_YORK)
                    .date()
                    .isoformat()
                    if source_timestamp
                    else fallback_date
                )
                points.append(
                    {
                        "ticker": ticker,
                        "price_date": price_date,
                        "close": close,
                        "volume": _optional_int(raw.get("volume")),
                        "source_timestamp": source_timestamp,
                    }
                )
    finally:
        if owns_client:
            client.close()
    return points


def fetch_history_points(
    ticker: str,
    *,
    api_key: str,
    from_date: str,
    to_date: str,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Fetch dated daily closes for one symbol, returned oldest to newest."""
    ticker = ticker.strip().upper()
    owns_client = client is None
    client = client or httpx.Client(timeout=45.0)
    try:
        body = _get_json(
            client,
            "historical-price-eod/full",
            params={
                "symbol": ticker,
                "from": from_date,
                "to": to_date,
                "apikey": api_key,
            },
        )
    finally:
        if owns_client:
            client.close()
    if not isinstance(body, list):
        raise FmpError(f"FMP history {ticker} returned invalid data")
    points: dict[str, dict[str, Any]] = {}
    for raw in body:
        if not isinstance(raw, dict):
            continue
        price_date = str(raw.get("date") or "").strip()
        close = _positive_float(raw.get("close"))
        if not price_date or close is None:
            continue
        points[price_date] = {
            "ticker": ticker,
            "price_date": price_date,
            "close": close,
            "volume": _optional_int(raw.get("volume")),
            "source_timestamp": None,
        }
    return [points[date] for date in sorted(points)]


def _get_json(
    client: httpx.Client,
    endpoint: str,
    *,
    params: dict[str, Any],
    attempts: int = 3,
) -> Any:
    url = f"{FMP_BASE}/{endpoint}"
    for attempt in range(attempts):
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            if attempt + 1 >= attempts:
                raise FmpError(f"FMP {endpoint} failed: {exc}") from exc
            time.sleep(2**attempt)
            continue
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as exc:
                raise FmpError(f"FMP {endpoint} returned invalid JSON") from exc
        if response.status_code != 429 and response.status_code < 500:
            raise FmpError(
                f"FMP {endpoint} failed: {response.status_code} {response.text[:200]}"
            )
        if attempt + 1 >= attempts:
            raise FmpError(
                f"FMP {endpoint} failed: {response.status_code} {response.text[:200]}"
            )
        time.sleep(2**attempt)
    raise FmpError(f"FMP {endpoint} failed")


def _parse_universe_row(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ticker = str(raw.get("symbol") or "").strip().upper()
    company_name = str(raw.get("companyName") or ticker).strip()
    exchange = str(raw.get("exchangeShortName") or raw.get("exchange") or "").upper()
    sector = str(raw.get("sector") or "").strip()
    industry = str(raw.get("industry") or "").strip()
    country = str(raw.get("country") or "").strip().upper()
    if (
        not ticker
        or not company_name
        or sector.lower() != "healthcare"
        or country not in {"US", "USA"}
        or exchange not in EXCHANGES
        or raw.get("isActivelyTrading") is False
        or raw.get("isEtf") is True
        or raw.get("isFund") is True
        or "warrant" in company_name.lower()
        or industry in NON_HEALTHCARE_INDUSTRIES
    ):
        return None
    return {
        "ticker": ticker,
        "company_name": company_name,
        "sector": "Healthcare",
        "industry": industry or None,
        "exchange": exchange,
        "country": "US",
        "market_cap": _optional_float(raw.get("marketCap")),
        "latest_price": _positive_float(raw.get("price")),
        "latest_volume": _optional_int(raw.get("volume")),
    }


def _positive_float(value: Any) -> float | None:
    parsed = _optional_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None

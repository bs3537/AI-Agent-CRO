"""FMP sell-side analyst price-target consensus client."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..news.fmp_client import FMP_BASE, FmpError

FMP_TARGET_WINDOW = "current_sell_side_consensus"
FMP_TARGET_DOCS_URL = (
    "https://site.financialmodelingprep.com/developer/docs/stable/"
    "price-target-consensus"
)


@dataclass(frozen=True)
class FmpConsensusTarget:
    mean_price_target: float
    high_price_target: float | None
    low_price_target: float | None
    analyst_count: int | None
    currency: str | None
    source_url: str = FMP_TARGET_DOCS_URL


def parse_fmp_consensus(ticker: str, body: Any) -> FmpConsensusTarget | None:
    """Parse one stable/price-target-consensus response."""
    row = body[0] if isinstance(body, list) and body else body
    if not isinstance(row, dict):
        return None
    mean = _positive_float(row.get("targetConsensus"))
    if mean is None:
        return None
    high = _positive_float(row.get("targetHigh"))
    low = _positive_float(row.get("targetLow"))
    if high is not None and high < mean:
        high = None
    if low is not None and low > mean:
        low = None
    analyst_count = _positive_int(
        row.get("analystCount")
        or row.get("numberOfAnalysts")
        or row.get("analysts")
    )
    currency = str(row.get("currency") or "USD").strip().upper() or "USD"
    return FmpConsensusTarget(
        mean_price_target=mean,
        high_price_target=high,
        low_price_target=low,
        analyst_count=analyst_count,
        currency=currency,
    )


def fetch_fmp_consensus(
    ticker: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
) -> FmpConsensusTarget | None:
    """Fetch the current FMP consensus target for one ticker."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        response = client.get(
            f"{FMP_BASE}/price-target-consensus",
            params={"symbol": ticker.strip().upper(), "apikey": api_key},
        )
        if response.status_code != 200:
            raise FmpError(
                f"FMP price-target {ticker.upper()} failed: "
                f"{response.status_code} {response.text[:200]}"
            )
        return parse_fmp_consensus(ticker, response.json())
    except (ValueError, httpx.HTTPError) as exc:
        raise FmpError(f"FMP price-target {ticker.upper()} failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

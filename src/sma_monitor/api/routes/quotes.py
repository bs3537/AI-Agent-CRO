"""GET /api/quotes — intraday price quotes for all held tickers.

Yahoo Finance is the primary source (~15-20 min delayed, no API key needed).
FMP is called in parallel as a cross-verification layer: when both sources
agree within 1% the quote is marked verified=True. When they diverge by more
than 1%, Yahoo's price wins and verified=False is returned so the UI can flag it.

Source fallback order:
  1. Yahoo primary + FMP cross-check  → source="yahoo+fmp"
  2. Yahoo only (FMP unreachable)      → source="yahoo_only"
  3. FMP only (Yahoo unreachable)      → source="fmp_only"
  4. Both fail                         → empty quotes, source="error"

Returns empty quotes (never an error response) when there are no holdings
or when all sources fail — the frontend falls back to EOD data cleanly.
Yahoo is attempted even when the FMP key is absent.

Market-open window: Mon–Fri, 09:30–16:00 US/Eastern (weekday + time-of-day
check; no holiday calendar).
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter

from ...config import settings
from ...news.fmp_client import FmpError, fetch_quotes
from ...portfolio.joined import latest_joined

router = APIRouter(prefix="/api", tags=["quotes"])

_log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_PRICE_TOLERANCE = 0.01  # flag divergence above 1%


def _is_market_open() -> bool:
    now = datetime.now(tz=_ET)
    if now.weekday() >= 5:
        return False
    return _MARKET_OPEN <= now.time() < _MARKET_CLOSE


def _fetch_yahoo_quotes(tickers: list[str], client: httpx.Client) -> dict[str, dict]:
    """Returns {TICKER: {price, change_pct}} from Yahoo Finance (~15-20 min delayed).

    Uses the public v7 quote API — no API key required. Times out after 10 s.
    Extracts regularMarketPrice and regularMarketChangePercent per symbol.
    Raises on any HTTP / parse error so the caller can treat Yahoo as optional.
    """
    symbols = ",".join(t.upper() for t in tickers)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
        ),
        "Accept": "application/json",
    }
    r = client.get(
        "https://query1.finance.yahoo.com/v7/finance/quote",
        params={"symbols": symbols},
        headers=headers,
        timeout=10.0,
    )
    r.raise_for_status()
    data = r.json()
    result: dict[str, dict] = {}
    for q in (data.get("quoteResponse") or {}).get("result") or []:
        sym = (q.get("symbol") or "").upper()
        price = q.get("regularMarketPrice")
        change_pct = q.get("regularMarketChangePercent")
        if sym and price is not None:
            result[sym] = {
                "price": float(price),
                "change_pct": float(change_pct) if change_pct is not None else 0.0,
            }
    return result


def _cross_verify(
    primary: dict[str, dict],
    secondary_prices: dict[str, float],
) -> dict[str, dict]:
    """Attaches verified flag to each primary quote by comparing against secondary prices.

    - If secondary has no price for a ticker: verified=False (primary kept as-is).
    - If prices agree within _PRICE_TOLERANCE: verified=True.
    - If they diverge: verified=False, warning logged (primary price kept —
      Yahoo is primary so no override needed).
    """
    out: dict[str, dict] = {}
    for ticker, data in primary.items():
        sec_price = secondary_prices.get(ticker)
        if sec_price is None:
            out[ticker] = {**data, "verified": False}
            continue
        primary_price = data["price"]
        divergence = abs(primary_price - sec_price) / sec_price if sec_price else 0.0
        if divergence > _PRICE_TOLERANCE:
            _log.warning(
                "quote_price_divergence",
                extra={
                    "ticker": ticker,
                    "yahoo": round(primary_price, 4),
                    "fmp": round(sec_price, 4),
                    "pct": round(divergence * 100, 2),
                },
            )
            out[ticker] = {**data, "verified": False}
        else:
            out[ticker] = {**data, "verified": True}
    return out


@router.get("/quotes")
def get_quotes() -> dict:
    is_open = _is_market_open()
    if not is_open:
        return {"quotes": {}, "is_market_open": False, "source": "market_closed"}
    api_key = settings.fmp_api_key
    try:
        holdings, _missing, _ = latest_joined()
        tickers = [h.ticker for h in holdings]
        if not tickers:
            return {"quotes": {}, "is_market_open": is_open, "source": "no_holdings"}

        with httpx.Client(timeout=15.0) as client:
            # --- Try Yahoo Finance as primary source (no API key required) ---
            yahoo_quotes: dict[str, dict] | None = None
            try:
                yahoo_quotes = _fetch_yahoo_quotes(tickers, client)
            except Exception as exc:
                _log.warning("yahoo_fetch_failed", extra={"err": str(exc)[:200]})

            # --- Try FMP as cross-verification layer (only when key is present) ---
            fmp_quotes: dict[str, dict] | None = None
            if api_key:
                try:
                    fmp_quotes = fetch_quotes(tickers, api_key=api_key, client=client)
                except FmpError as exc:
                    _log.warning("fmp_fetch_failed", extra={"err": str(exc)[:200]})

            # --- Merge: Yahoo primary, FMP secondary ---
            if yahoo_quotes and fmp_quotes:
                fmp_prices = {t: d["price"] for t, d in fmp_quotes.items()}
                quotes = _cross_verify(yahoo_quotes, fmp_prices)
                source = "yahoo+fmp"
            elif yahoo_quotes:
                quotes = {t: {**d, "verified": False} for t, d in yahoo_quotes.items()}
                source = "yahoo_only"
            elif fmp_quotes:
                quotes = {t: {**d, "verified": False} for t, d in fmp_quotes.items()}
                source = "fmp_only"
            else:
                return {"quotes": {}, "is_market_open": is_open, "source": "error"}

        return {"quotes": quotes, "is_market_open": is_open, "source": source}

    except Exception as exc:
        _log.exception("quotes_unexpected_error", extra={"err": str(exc)[:200]})
        return {"quotes": {}, "is_market_open": is_open, "source": "error"}

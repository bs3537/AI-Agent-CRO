"""GET /api/quotes — intraday price quotes for all held tickers.

Calls FMP /stable/quote for every active holding and returns the current
price map (with daily change %) plus an is_market_open flag. Each quote is
cross-checked against Yahoo Finance (15-20 min delayed, no API key needed):
when the two sources diverge by more than 1%, the Yahoo price is used and
the quote is marked verified=False so the UI can flag it.

Returns empty quotes (never an error) when the FMP key is absent, when there
are no holdings, or when FMP fails — the frontend falls back to EOD data
cleanly in all these cases.

Market-open window: Mon–Fri, 09:30–16:00 US/Eastern (weekday + time-of-day
check; no holiday calendar — see follow-up task for that).
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
_PRICE_TOLERANCE = 0.01  # flag FMP/Yahoo divergence above 1%


def _is_market_open() -> bool:
    now = datetime.now(tz=_ET)
    if now.weekday() >= 5:
        return False
    return _MARKET_OPEN <= now.time() < _MARKET_CLOSE


def _fetch_yahoo_prices(tickers: list[str], client: httpx.Client) -> dict[str, float]:
    """Returns {TICKER: regularMarketPrice} from Yahoo Finance (~15-20 min delayed).

    Uses the public v7 quote API — no API key required. Times out after 10 s.
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
    result: dict[str, float] = {}
    for q in (data.get("quoteResponse") or {}).get("result") or []:
        sym = (q.get("symbol") or "").upper()
        price = q.get("regularMarketPrice")
        if sym and price is not None:
            result[sym] = float(price)
    return result


def _verify_quotes(
    fmp: dict[str, dict], yahoo: dict[str, float]
) -> dict[str, dict]:
    """Cross-checks FMP prices against Yahoo.

    For each ticker:
    - If Yahoo has no price: verified=False (FMP price kept).
    - If prices agree within _PRICE_TOLERANCE: verified=True.
    - If they diverge: Yahoo price wins, verified=False, warning logged.
    """
    out: dict[str, dict] = {}
    for ticker, data in fmp.items():
        yahoo_price = yahoo.get(ticker)
        if yahoo_price is None:
            out[ticker] = {**data, "verified": False}
            continue
        fmp_price = data["price"]
        divergence = abs(fmp_price - yahoo_price) / yahoo_price if yahoo_price else 0.0
        if divergence > _PRICE_TOLERANCE:
            _log.warning(
                "quote_price_divergence",
                extra={
                    "ticker": ticker,
                    "fmp": round(fmp_price, 4),
                    "yahoo": round(yahoo_price, 4),
                    "pct": round(divergence * 100, 2),
                },
            )
            out[ticker] = {**data, "price": yahoo_price, "verified": False}
        else:
            out[ticker] = {**data, "verified": True}
    return out


@router.get("/quotes")
def get_quotes() -> dict:
    is_open = _is_market_open()
    api_key = settings.fmp_api_key
    if not api_key:
        return {"quotes": {}, "is_market_open": is_open, "source": "no_key"}
    try:
        holdings, _missing, _ = latest_joined()
        tickers = [h.ticker for h in holdings]
        if not tickers:
            return {"quotes": {}, "is_market_open": is_open, "source": "no_holdings"}
        with httpx.Client(timeout=15.0) as client:
            fmp_quotes = fetch_quotes(tickers, api_key=api_key, client=client)
            try:
                yahoo_prices = _fetch_yahoo_prices(tickers, client)
                quotes = _verify_quotes(fmp_quotes, yahoo_prices)
                source = "fmp+yahoo"
            except Exception as exc:
                _log.warning("yahoo_fetch_failed", extra={"err": str(exc)[:200]})
                quotes = {t: {**d, "verified": False} for t, d in fmp_quotes.items()}
                source = "fmp_only"
        return {"quotes": quotes, "is_market_open": is_open, "source": source}
    except FmpError as exc:
        _log.warning("quotes_fmp_error", extra={"err": str(exc)[:200]})
        return {"quotes": {}, "is_market_open": is_open, "source": "fmp_error"}
    except Exception as exc:
        _log.exception("quotes_unexpected_error", extra={"err": str(exc)[:200]})
        return {"quotes": {}, "is_market_open": is_open, "source": "error"}

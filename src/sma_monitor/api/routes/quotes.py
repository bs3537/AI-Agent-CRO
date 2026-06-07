"""GET /api/quotes — intraday price quotes for all held tickers.

Calls FMP /stable/quote for every active holding and returns the current
price map plus an is_market_open flag so the frontend knows whether to show
a LIVE badge.  Returns empty quotes (not an error) when the FMP key is absent,
when there are no holdings, or when FMP fails — the frontend falls back to EOD
data cleanly in all these cases.

Market-open window: Mon–Fri, 09:30–16:00 US/Eastern (no holiday calendar;
weekday + time-of-day check is sufficient).
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


def _is_market_open() -> bool:
    now = datetime.now(tz=_ET)
    if now.weekday() >= 5:
        return False
    return _MARKET_OPEN <= now.time() < _MARKET_CLOSE


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
            quotes = fetch_quotes(tickers, api_key=api_key, client=client)
        return {"quotes": quotes, "is_market_open": is_open, "source": "fmp"}
    except FmpError as exc:
        _log.warning("quotes_fmp_error", extra={"err": str(exc)[:200]})
        return {"quotes": {}, "is_market_open": is_open, "source": "fmp_error"}
    except Exception as exc:
        _log.exception("quotes_unexpected_error", extra={"err": str(exc)[:200]})
        return {"quotes": {}, "is_market_open": is_open, "source": "error"}

"""IBKR Flex Web Service client + XML parser.

Two-step protocol (https://www.interactivebrokers.com/api/doc.html — Flex Web Service):
  1. GET SendRequest(t=token, q=query_id, v=3)            -> ReferenceCode
  2. GET GetStatement(t=token, q=ReferenceCode, v=3)      -> FlexQueryResponse XML
     (may return ErrorCode 1019 'statement generation in progress' — poll.)

IBKR enforces a minimum re-run window per Flex Query (typically ~5–15 min);
the scheduler in Phase 6 owns the cadence — this module just executes one pull.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .schema import Position

FLEX_BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
SEND_URL = f"{FLEX_BASE}/SendRequest"
GET_URL = f"{FLEX_BASE}/GetStatement"
API_VERSION = "3"

# IBKR error codes worth handling distinctly.
ERR_STATEMENT_IN_PROGRESS = "1019"


class FlexError(RuntimeError):
    pass


@dataclass
class FlexRawStatement:
    xml: str
    pulled_at: datetime


def fetch_statement(
    token: str,
    query_id: str,
    *,
    max_poll_seconds: int = 60,
    poll_interval: float = 5.0,
    client: httpx.Client | None = None,
) -> FlexRawStatement:
    """Send + poll. Returns the raw XML body once available."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        send = client.get(SEND_URL, params={"t": token, "q": query_id, "v": API_VERSION})
        send.raise_for_status()
        ref, status = _parse_send_response(send.text)
        if status != "Success" or not ref:
            raise FlexError(f"SendRequest failed: status={status!r} body={send.text[:500]}")

        deadline = time.monotonic() + max_poll_seconds
        while True:
            got = client.get(GET_URL, params={"t": token, "q": ref, "v": API_VERSION})
            got.raise_for_status()
            text = got.text
            if "<FlexQueryResponse" in text:
                return FlexRawStatement(xml=text, pulled_at=datetime.now(timezone.utc))
            err = _parse_get_error_code(text)
            if err == ERR_STATEMENT_IN_PROGRESS:
                if time.monotonic() >= deadline:
                    raise FlexError("Timed out waiting for Flex statement to generate")
                time.sleep(poll_interval)
                continue
            raise FlexError(f"GetStatement failed: code={err!r} body={text[:500]}")
    finally:
        if owns_client:
            client.close()


def load_xml_from_file(path: Path) -> FlexRawStatement:
    """Replay a saved Flex XML — useful for tests and offline runs."""
    return FlexRawStatement(
        xml=path.read_text(),
        pulled_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
    )


# --- Parsing -----------------------------------------------------------------


def parse_positions(xml_text: str, *, pulled_at: datetime) -> tuple[list[Position], float]:
    """Parse FlexQueryResponse XML → (positions, NAV).

    NAV is sourced from <EquitySummaryInBase total=...> when present, then
    <EquitySummaryByReportDateInBase> (most recent reportDate), then
    <ChangeInNAV endingValue=...>. The Flex Query template must enable at
    least one of these sections — fail loudly if none are found.
    """
    root = ET.fromstring(xml_text)

    nav = _extract_nav(root)
    if nav is None or nav <= 0:
        raise FlexError(
            "No NAV in Flex statement. Enable 'Cash Report / Equity Summary' "
            "or 'Change in NAV' in the Flex Query template."
        )

    positions: list[Position] = []
    for op in root.iter("OpenPosition"):
        symbol = (op.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        qty = _to_float(op.get("position")) or 0.0
        mv = _to_float(op.get("positionValue")) or 0.0
        cb_raw = op.get("costBasisMoney") or op.get("costBasis")
        cb = _to_float(cb_raw) if cb_raw else None
        positions.append(
            Position(
                ticker=symbol,
                qty=qty,
                market_value=mv,
                pct_nav=mv / nav,
                cost_basis=cb,
                pulled_at=pulled_at,
                nav=nav,
            )
        )
    return _collapse_by_ticker(positions, nav), nav


def _parse_send_response(xml_text: str) -> tuple[str, str]:
    root = ET.fromstring(xml_text)
    return (
        (root.findtext("ReferenceCode") or "").strip(),
        (root.findtext("Status") or "").strip(),
    )


def _parse_get_error_code(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    code = (root.findtext("ErrorCode") or "").strip()
    return code or None


def _extract_nav(root: ET.Element) -> float | None:
    eq = next(iter(root.iter("EquitySummaryInBase")), None)
    if eq is not None:
        v = _to_float(eq.get("total"))
        if v:
            return v
    # By-date variant — take the row with the latest reportDate.
    by_date = sorted(
        root.iter("EquitySummaryByReportDateInBase"),
        key=lambda el: el.get("reportDate") or "",
    )
    if by_date:
        v = _to_float(by_date[-1].get("total"))
        if v:
            return v
    cn = next(iter(root.iter("ChangeInNAV")), None)
    if cn is not None:
        v = _to_float(cn.get("endingValue"))
        if v:
            return v
    return None


def _to_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _collapse_by_ticker(positions: list[Position], nav: float) -> list[Position]:
    """Sum rows that share a ticker (long+short legs, multi-account lots)."""
    by_ticker: dict[str, Position] = {}
    for p in positions:
        prev = by_ticker.get(p.ticker)
        if prev is None:
            by_ticker[p.ticker] = p
            continue
        merged_cb: float | None
        if prev.cost_basis is None and p.cost_basis is None:
            merged_cb = None
        else:
            merged_cb = (prev.cost_basis or 0.0) + (p.cost_basis or 0.0)
        mv = prev.market_value + p.market_value
        by_ticker[p.ticker] = Position(
            ticker=p.ticker,
            qty=prev.qty + p.qty,
            market_value=mv,
            pct_nav=mv / nav,
            cost_basis=merged_cb,
            pulled_at=p.pulled_at,
            nav=nav,
        )
    # Sorted by %NAV desc — useful for human inspection.
    return sorted(by_ticker.values(), key=lambda p: p.pct_nav, reverse=True)

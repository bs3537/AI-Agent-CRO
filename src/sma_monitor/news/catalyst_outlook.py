"""Cached multi-sector upcoming-catalyst research."""
from __future__ import annotations

import calendar
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, field_validator

from ..db import connection, init_db
from ..identity import event_id
from ..llm import get_provider
from ..llm.throughput import llm_concurrency, map_concurrent
from .fmp_client import latest_fmp_metrics

log = logging.getLogger("sma_monitor.news.catalyst_outlook")

MAX_ITEMS = 3
CATALYST_STAGE = "catalyst_outlook"
CATALYST_TIMEOUT_S = 300
CATALYST_TYPES = (
    "regulatory",
    "clinical_data",
    "product_launch",
    "earnings",
    "investor_event",
    "contract_milestone",
    "fund_event",
    "other",
)

CATALYST_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalyst_outlooks (
    event_id       TEXT PRIMARY KEY,
    ticker         TEXT NOT NULL,
    items_json     TEXT NOT NULL,
    model_used     TEXT,
    searched_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalyst_outlook_ticker
    ON catalyst_outlooks(ticker);
CREATE INDEX IF NOT EXISTS idx_catalyst_outlook_searched_at
    ON catalyst_outlooks(searched_at);
"""

CATALYST_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "date": {"type": ["string", "null"]},
                    "date_label": {"type": "string"},
                    "type": {"type": "string", "enum": list(CATALYST_TYPES)},
                    "label": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                    "source_title": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": [
                    "date",
                    "date_label",
                    "type",
                    "label",
                    "confirmed",
                    "source_title",
                    "source_url",
                ],
            },
        }
    },
    "required": ["items"],
}


class CatalystOutlookItem(BaseModel):
    date: str | None = None
    date_label: str
    type: Literal[
        "regulatory",
        "clinical_data",
        "product_launch",
        "earnings",
        "investor_event",
        "contract_milestone",
        "fund_event",
        "other",
    ]
    label: str
    confirmed: bool = False
    source_title: str
    source_url: str

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return date.fromisoformat(value.strip()).isoformat()

    @field_validator("date_label", "label", "source_title", "source_url")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("label")
    @classmethod
    def _truncate_label(cls, value: str) -> str:
        return value[:80]


def init_catalyst_schema() -> None:
    init_db()
    with connection() as conn:
        conn.executescript(CATALYST_SCHEMA)


def catalyst_event_id(ticker: str, searched_at: datetime) -> str:
    return event_id({
        "kind": "catalyst_outlook",
        "ticker": ticker.strip().upper(),
        "date": searched_at.date().isoformat(),
    })


def save_catalyst_outlook(
    ticker: str,
    items: list[CatalystOutlookItem | dict[str, Any]],
    *,
    model_used: str | None = None,
    searched_at: datetime | None = None,
) -> str:
    init_catalyst_schema()
    ticker = ticker.strip().upper()
    searched_at = searched_at or datetime.now(UTC)
    ordered = _ordered_clamped(items)
    eid = catalyst_event_id(ticker, searched_at)
    payload = json.dumps([item.model_dump(mode="json") for item in ordered])
    with connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO events(event_id, kind, ticker, first_seen, payload)
               VALUES (?, ?, ?, ?, ?)""",
            (
                eid,
                "catalyst_outlook",
                ticker,
                searched_at.isoformat(),
                json.dumps({"items": len(ordered), "model_used": model_used}),
            ),
        )
        conn.execute(
            """INSERT OR REPLACE INTO catalyst_outlooks
               (event_id, ticker, items_json, model_used, searched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (eid, ticker, payload, model_used, searched_at.isoformat()),
        )
    return eid


def latest_catalyst_outlook(ticker: str) -> list[dict[str, Any]]:
    init_catalyst_schema()
    with connection() as conn:
        row = conn.execute(
            """SELECT items_json FROM catalyst_outlooks
               WHERE ticker = ? ORDER BY searched_at DESC LIMIT 1""",
            (ticker.strip().upper(),),
        ).fetchone()
    if row is None:
        return []
    try:
        items = json.loads(row["items_json"])
    except (TypeError, json.JSONDecodeError):
        return []
    return items if isinstance(items, list) else []


def build_catalyst_prompt(
    holding,
    *,
    is_etf: bool = False,
    today: date | None = None,
) -> tuple[str, str]:
    """Build a source-required, sector-neutral 18-month catalyst prompt."""
    today = today or datetime.now(UTC).date()
    window_end = _plus_months(today, 18)
    company = holding.company_name or holding.ticker
    sector = (
        holding.preliminary_thesis.sector
        if holding.preliminary_thesis is not None
        else None
    )
    security_rules = (
        "This is an ETF/fund. Qualifying events are official index reconstitutions, "
        "methodology changes, scheduled distributions, fund reorganizations, closures, "
        "or issuer-announced fund events. Do not report company earnings or analyst "
        "price targets."
        if is_etf
        else
        "This is an operating company. Qualifying events include earnings, officially "
        "guided product launches, clinical data, regulatory decisions/submissions, "
        "investor days, and contract or commercial milestones. Select the events most "
        "material to this specific company and sector."
    )
    system = (
        "You research upcoming catalysts for a multi-sector long-only portfolio. "
        "Use Codex native web search. Include only future events supported by an "
        "official company, regulator, exchange, trial registry, or fund-issuer source. "
        "Never invent a date or treat analyst speculation as confirmation. Return only "
        "the requested JSON."
    )
    user = (
        f"Ticker: {holding.ticker}\n"
        f"Company/fund: {company}\n"
        f"Sector: {sector or 'unknown'}\n"
        f"Portfolio stage label: {holding.stage}\n"
        f"Research window: {today.isoformat()} through {window_end.isoformat()}\n\n"
        f"{security_rules}\n\n"
        f"Return at most {MAX_ITEMS} material events, soonest first. For an exact "
        "date, use ISO YYYY-MM-DD and set confirmed=true only when the primary source "
        "publishes that date. For a guided quarter/half/year, set date=null, put the "
        "window in date_label, and confirmed=false. Include a concise source_title and "
        "direct source_url for every item. If no event qualifies, return an empty list."
    )
    return system, user


def research_catalyst_outlook(
    holding,
    provider,
    *,
    is_etf: bool = False,
) -> list[CatalystOutlookItem]:
    system, user = build_catalyst_prompt(holding, is_etf=is_etf)
    raw = provider.complete_json(
        system=system,
        user=user,
        schema=CATALYST_JSON_SCHEMA,
        max_tokens=1200,
        timeout_s=CATALYST_TIMEOUT_S,
    )
    items = raw.get("items") if isinstance(raw, dict) else []
    return _ordered_clamped(items if isinstance(items, list) else [])


def refresh_catalyst_outlooks_for_holdings(
    *,
    tickers: list[str] | None = None,
    from_file: Path | None = None,
    provider=None,
    workers: int | None = None,
) -> dict[str, Any]:
    """Refresh held tickers without clearing last-success rows on failures."""
    from ..portfolio.joined import latest_joined

    init_catalyst_schema()
    holdings, _missing, _pulled_at = latest_joined()
    wanted = {
        ticker.strip().upper()
        for ticker in (tickers or [])
        if ticker and ticker.strip()
    }
    selected = [holding for holding in holdings if not wanted or holding.ticker in wanted]
    state: dict[str, Any] = {
        "tickers": len(selected),
        "updated": 0,
        "errors": 0,
        "failed": [],
        "source": "fixture" if from_file is not None else "codex",
    }
    if not selected:
        return state

    if from_file is not None:
        raw = json.loads(Path(from_file).read_text())
        by_ticker = {str(ticker).upper(): value for ticker, value in raw.items()}
        for holding in selected:
            if holding.ticker not in by_ticker:
                continue
            save_catalyst_outlook(
                holding.ticker,
                by_ticker[holding.ticker],
                model_used="fixture",
            )
            state["updated"] += 1
        return state

    provider = provider or get_provider(prefer_offline=False, stage=CATALYST_STAGE)
    if provider is None:
        return {
            **state,
            "status": "skipped",
            "reason": "codex unavailable",
        }

    worker_count = max(1, min(4, workers or llm_concurrency()))
    state["workers"] = worker_count
    state["model_used"] = getattr(provider, "model_label", "unknown")
    for start in range(0, len(selected), worker_count):
        batch = selected[start : start + worker_count]

        def _research(holding):
            is_etf = bool(
                (holding.preliminary_thesis is not None
                 and holding.preliminary_thesis.security_type == "etf")
                or (latest_fmp_metrics(holding.ticker) or {}).get("is_etf")
            )
            return research_catalyst_outlook(
                holding,
                provider,
                is_etf=is_etf,
            )

        results = map_concurrent(_research, batch, workers=worker_count)
        for holding, (items, error) in zip(batch, results, strict=True):
            if error is not None:
                state["errors"] += 1
                state["failed"].append({
                    "ticker": holding.ticker,
                    "reason": type(error).__name__,
                })
                log.warning(
                    "catalyst_outlook_failed",
                    extra={"ticker": holding.ticker, "error": str(error)[:240]},
                )
                continue
            save_catalyst_outlook(
                holding.ticker,
                items,
                model_used=state["model_used"],
            )
            state["updated"] += 1
    return state


def _ordered_clamped(
    items: list[CatalystOutlookItem | dict[str, Any]] | None,
) -> list[CatalystOutlookItem]:
    valid: list[CatalystOutlookItem] = []
    for item in items or []:
        try:
            valid.append(
                item
                if isinstance(item, CatalystOutlookItem)
                else CatalystOutlookItem.model_validate(item)
            )
        except (ValidationError, ValueError):
            continue
    dated = sorted((item for item in valid if item.date), key=lambda item: item.date or "")
    undated = [item for item in valid if item.date is None]
    return (dated + undated)[:MAX_ITEMS]


def _plus_months(value: date, months: int) -> date:
    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

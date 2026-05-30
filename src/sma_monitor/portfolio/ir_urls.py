"""Populate sidecar IR URLs from live source discovery."""
from __future__ import annotations

import logging
from dataclasses import asdict

from ..config import settings
from ..news.fmp_client import fetch_metrics, latest_fmp_metrics
from ..news.ir_discovery import discover_ir_urls
from .sidecar import ensure_sidecar, load_sidecar, set_ir_urls
from .store import latest_positions

log = logging.getLogger("sma_monitor.portfolio.ir_urls")


def populate_ir_urls_for_tickers(
    tickers: list[str],
    *,
    create_missing: bool = False,
    force: bool = False,
) -> dict:
    """Discover and persist IR URLs for specific tickers."""
    out: dict = {"checked": 0, "updated": 0, "skipped": 0, "not_found": 0, "results": {}}
    for raw in tickers:
        ticker = raw.strip().upper()
        if not ticker:
            continue
        sc = ensure_sidecar(ticker) if create_missing else load_sidecar(ticker)
        if sc is None:
            out["skipped"] += 1
            out["results"][ticker] = {"status": "skipped", "reason": "missing_sidecar"}
            continue
        if (
            not force
            and sc.ir_url
            and (sc.press_releases_url or sc.press_release_rss_url)
        ):
            out["skipped"] += 1
            out["results"][ticker] = {"status": "skipped", "reason": "already_populated"}
            continue

        company_name = _company_name_for(ticker, existing=sc.company_name)
        if company_name and not sc.company_name:
            set_ir_urls(ticker, company_name=company_name, create=create_missing)

        out["checked"] += 1
        result = discover_ir_urls(ticker=ticker, company_name=company_name)
        out["results"][ticker] = asdict(result)
        if not result.found_any:
            out["not_found"] += 1
            continue
        set_ir_urls(
            ticker,
            ir_url=result.ir_url,
            press_releases_url=result.press_releases_url,
            press_release_rss_url=result.press_release_rss_url,
            company_name=company_name,
            create=create_missing,
        )
        out["updated"] += 1
        log.info("ir_urls_populated", extra={"ticker": ticker, **asdict(result)})
    return out


def populate_ir_urls_for_current_positions(
    *,
    create_missing: bool = True,
    force: bool = False,
) -> dict:
    """Discover IR URLs for every ticker in the latest broker/manual position set."""
    positions, pulled_at = latest_positions()
    tickers = [p.ticker for p in positions]
    state = populate_ir_urls_for_tickers(
        tickers,
        create_missing=create_missing,
        force=force,
    )
    state["positions"] = len(positions)
    state["pulled_at"] = pulled_at.isoformat() if pulled_at else None
    return state


def _company_name_for(ticker: str, *, existing: str | None) -> str | None:
    """Resolve a ticker-only sidecar before search, avoiding APP→Apple-style mistakes."""
    if existing:
        return existing
    metrics = latest_fmp_metrics(ticker) or {}
    company = metrics.get("company")
    if company:
        return str(company)
    if not settings.fmp_api_key:
        return None
    try:
        metrics = fetch_metrics(ticker, api_key=settings.fmp_api_key)
    except Exception as e:  # noqa: BLE001 - IR discovery can still try ticker-only.
        log.warning("ir_company_name_lookup_failed", extra={"ticker": ticker, "err": str(e)[:200]})
        return None
    company = metrics.get("company")
    return str(company) if company else None

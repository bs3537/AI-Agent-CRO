"""SEC EDGAR filings adapter (financials primary, ahead of FMP).

Fetches a holding's recent SEC filings (10-K/10-Q/8-K/S-1/424B/SC 13D ...) from
EDGAR and returns them as ExaResults, so regulatory/primary financial disclosures
flow into the pipeline as tier-1 evidence (source_tiers maps sec.gov -> tier 1).
Per the due-diligence source policy, SEC filings are the PRIMARY financial
source; FMP metrics are the secondary aggregated view (see news/source_policy).

Auth: none — EDGAR is free, but its fair-access policy requires a descriptive
User-Agent with contact info (SEC_EDGAR_USER_AGENT). Two GETs per holding:
resolve ticker->CIK via the public company_tickers map, then pull the company
submissions JSON. Offline replay via load_response_file mirrors the other adapters.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .exa_client import ExaResult

# EDGAR endpoints: the public ticker->CIK map and the per-company submissions
# feed. Filing documents live under the Archives path built in _parse_response.
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"

# Material filing forms worth surfacing for thesis drift (annual/quarterly, 8-K
# events, offerings/dilution, ownership). Other forms are filtered out as noise.
DEFAULT_FORMS = ("10-K", "10-Q", "8-K", "S-1", "424B5", "424B4", "6-K", "20-F", "SC 13D")


# Exception for any non-200 / unparseable EDGAR response. Caught by the caller so
# a failed lookup records an error and the cycle continues.
class SecError(RuntimeError):
    pass


# Process-level cache of EDGAR's ticker->CIK map (~1 MB). Fetched once and
# reused so a per-holding loop doesn't re-download it for every ticker.
_CIK_MAP: dict[str, int] | None = None


# Fetch (once) and return EDGAR's ticker -> CIK map. Cached at module level for
# the process lifetime; the map changes rarely.
def _ticker_cik_map(*, user_agent: str, client: httpx.Client) -> dict[str, int]:
    global _CIK_MAP
    if _CIK_MAP is None:
        resp = client.get(
            SEC_TICKERS_URL, headers={"User-Agent": user_agent, "Accept": "application/json"}
        )
        if resp.status_code != 200:
            raise SecError(f"EDGAR ticker map failed: {resp.status_code}")
        _CIK_MAP = {
            (row.get("ticker") or "").upper(): int(row["cik_str"])
            for row in resp.json().values()
            if row.get("ticker")
        }
    return _CIK_MAP


# Resolve a ticker to its CIK integer via the cached company_tickers map.
# Returns None when the ticker isn't listed (caller treats as "no filings").
def resolve_cik(ticker: str, *, user_agent: str, client: httpx.Client) -> int | None:
    return _ticker_cik_map(user_agent=user_agent, client=client).get(ticker.strip().upper())


# Fetch a holding's recent SEC filings and return ExaResults. Resolves the CIK,
# pulls the submissions feed, and parses the most recent `num_results` filings
# (restricted to material `forms`). Signature mirrors the other source adapters.
def search(
    ticker: str,
    *,
    user_agent: str,
    num_results: int = 5,
    forms: tuple[str, ...] = DEFAULT_FORMS,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """One holding's recent SEC filings -> list[ExaResult] (financials primary)."""
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        cik = resolve_cik(ticker, user_agent=user_agent, client=client)
        if cik is None:
            return []
        resp = client.get(
            SEC_SUBMISSIONS_URL.format(cik=cik),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise SecError(
                f"EDGAR submissions failed for {ticker} (CIK {cik}): {resp.status_code}"
            )
        return _parse_response(resp.json(), num_results=num_results, forms=forms)
    finally:
        if owns:
            client.close()


# Load a saved EDGAR submissions JSON from disk — offline replay.
def load_response_file(
    path: Path, *, num_results: int = 5, forms: tuple[str, ...] = DEFAULT_FORMS
) -> list[ExaResult]:
    return _parse_response(json.loads(path.read_text()), num_results=num_results, forms=forms)


# Parse an EDGAR submissions JSON into ExaResults. filings.recent stores parallel
# arrays (newest-first); zip them, keep material forms, build the canonical
# Archives URL (sec.gov -> tier 1), and cap at num_results.
def _parse_response(
    body: dict[str, Any], *, num_results: int, forms: tuple[str, ...]
) -> list[ExaResult]:
    cik = int(body.get("cik") or 0)
    name = (body.get("name") or "").strip()
    recent = (body.get("filings") or {}).get("recent") or {}
    accession = recent.get("accessionNumber") or []
    fdate = recent.get("filingDate") or []
    form = recent.get("form") or []
    primary = recent.get("primaryDocument") or []
    desc = recent.get("primaryDocDescription") or []

    out: list[ExaResult] = []
    for i in range(len(accession)):
        f = form[i] if i < len(form) else ""
        if forms and f not in forms:
            continue
        acc = (accession[i] or "").replace("-", "")
        doc = primary[i] if i < len(primary) else ""
        filed = fdate[i] if i < len(fdate) else None
        d = desc[i] if i < len(desc) else ""
        url = (
            SEC_ARCHIVE.format(cik=cik, accession=acc, doc=doc)
            if acc and doc
            else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
        )
        out.append(
            ExaResult(
                title=f"{f} — {name}" if name else f,
                url=url,
                published_at=_parse_date(filed),
                excerpt=f"{f} filed {filed or '?'}. {d}".strip(),
                score=None,
                raw={
                    "form": f, "filingDate": filed, "accessionNumber": accession[i],
                    "primaryDocument": doc, "primaryDocDescription": d,
                    "cik": cik, "name": name,
                },
            )
        )
        if len(out) >= num_results:
            break
    return out


# Parse an EDGAR filing date (YYYY-MM-DD) into a datetime, or None.
def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None

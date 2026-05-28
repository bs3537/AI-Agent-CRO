"""Semantic Scholar literature adapter (W2 — bucket #10, Scientific Literature & KOL).

Searches peer-reviewed literature for a holding's drugs/indications via the
Semantic Scholar Graph API and returns the same ExaResult shape the pipeline
consumes. Each hit's URL is built as https://doi.org/{doi} when a DOI is present
(so source_tiers classifies it tier-3 peer-reviewed), falling back to the
Semantic Scholar paper page otherwise.

Auth: an API key sent as the `x-api-key` header (SEMANTIC_SCHOLAR_API_KEY).
Unlike an OAuth/MCP integration the key never expires or logs out, so this runs
unattended on a headless host — which is why it replaces the earlier Scite
adapter for #10. Until the key (or a --from-file fixture) is set, poll_literature
is skipped, so this is never called live without it.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .exa_client import ExaResult

# Semantic Scholar Graph API paper-search endpoint + the fields we request.
# `fields` is required — without it the API returns only paperId + title.
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,year,publicationDate,externalIds,url,venue,citationCount,authors"


# Exception for any non-200 / unparseable Semantic Scholar response. Caught by
# the literature poll so a failed query records an error row and the cycle goes on.
class SemanticScholarError(RuntimeError):
    pass


# Execute one Semantic Scholar paper search and return ExaResults. `term` is the
# drug/indication query (e.g. "exa-cel sickle cell disease"). Signature mirrors
# the other source adapters so the pipeline treats it uniformly.
def search(
    term: str,
    *,
    api_key: str,
    num_results: int = 5,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """One Semantic Scholar paper search → list[ExaResult] (bucket #10)."""
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        resp = client.get(
            S2_SEARCH,
            params={"query": term, "limit": num_results, "fields": S2_FIELDS},
            headers={"x-api-key": api_key, "Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise SemanticScholarError(
                f"Semantic Scholar search failed: {resp.status_code} {resp.text[:300]}"
            )
        return _parse_response(resp.json())
    finally:
        if owns:
            client.close()


# Load a saved Semantic Scholar response JSON from disk — offline replay.
def load_response_file(path: Path) -> list[ExaResult]:
    return _parse_response(json.loads(path.read_text()))


# Parse a Semantic Scholar response into ExaResults. Builds the canonical
# https://doi.org/{doi} link when a DOI is present (→ tier-3 peer-reviewed),
# else falls back to the Semantic Scholar paper page. Tolerant of null fields.
def _parse_response(body: dict[str, Any]) -> list[ExaResult]:
    out: list[ExaResult] = []
    for r in body.get("data") or []:
        ext = r.get("externalIds") or {}
        doi = (ext.get("DOI") or "").strip()
        url = f"https://doi.org/{doi}" if doi else (r.get("url") or "").strip()
        venue = (r.get("venue") or "").strip()
        title = (r.get("title") or "").strip()
        out.append(
            ExaResult(
                title=f"{title} ({venue})" if venue else title,
                url=url,
                published_at=_parse_year(r.get("publicationDate") or r.get("year")),
                excerpt=(r.get("abstract") or "").strip(),
                score=r.get("citationCount"),
                raw=r,
            )
        )
    return out


# Parse a Semantic Scholar date that may be an ISO date ("2024-05-01"), a bare
# year (int/str), or None. Returns a datetime (Jan 1 for bare years) or None.
def _parse_year(v: Any) -> datetime | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit() and len(s) == 4:
        try:
            return datetime(int(s), 1, 1)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

"""Scite literature adapter (W2 — bucket #10, Scientific Literature & KOL).

Searches peer-reviewed literature for a holding's drugs/indications and returns
the same ExaResult shape the pipeline consumes, with each hit's URL built as
https://doi.org/{doi} (so source_tiers classifies it as tier-3 peer-reviewed).

Auth: a bearer token (SCITE_API_KEY). NOTE: the exact Scite REST search path
and field names should be confirmed against current Scite API docs — they are
isolated here in SCITE_SEARCH + _parse_response so wiring them up when the key
arrives is a one-spot change. The response parser is deliberately tolerant of
field-name variants. Until the key is set, poll_literature is skipped, so this
is never called live without it.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .exa_client import ExaResult

# Scite literature search endpoint. CONFIRM against Scite's current API docs
# when wiring the live key — kept here as the single point to adjust.
SCITE_SEARCH = "https://api.scite.ai/search"


# Exception for any non-200 / unparseable Scite response. Caught by the
# literature poll so a failed query records an error row and the cycle goes on.
class SciteError(RuntimeError):
    pass


# Execute one Scite literature search and return ExaResults. `term` is the
# drug/indication query (e.g. "vanzacaftor cystic fibrosis"). Signature mirrors
# the other source adapters so the pipeline treats it uniformly.
def search(
    term: str,
    *,
    api_key: str,
    num_results: int = 5,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """One Scite literature search → list[ExaResult] (bucket #10)."""
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        resp = client.get(
            SCITE_SEARCH,
            params={"term": term, "limit": num_results, "mode": "all"},
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise SciteError(f"Scite search failed: {resp.status_code} {resp.text[:300]}")
        return _parse_response(resp.json())
    finally:
        if owns:
            client.close()


# Load a saved Scite response JSON from disk — offline replay.
def load_response_file(path: Path) -> list[ExaResult]:
    return _parse_response(json.loads(path.read_text()))


# Parse a Scite response into ExaResults. Tolerant of field-name variants
# (hits/results/papers; abstract/snippet/text; year/publicationYear/date).
# Builds the canonical https://doi.org/{doi} link per the Scite usage guide.
def _parse_response(body: dict[str, Any]) -> list[ExaResult]:
    items = body.get("hits") or body.get("results") or body.get("papers") or []
    out: list[ExaResult] = []
    for r in items:
        doi = (r.get("doi") or "").strip()
        url = f"https://doi.org/{doi}" if doi else (r.get("url") or "").strip()
        excerpt = (r.get("abstract") or r.get("snippet") or r.get("text") or "").strip()
        journal = (r.get("journal") or r.get("source") or "").strip()
        title = (r.get("title") or "").strip()
        out.append(
            ExaResult(
                title=f"{title} ({journal})" if journal else title,
                url=url,
                published_at=_parse_year(
                    r.get("publicationYear") or r.get("year") or r.get("date") or r.get("publishedDate")
                ),
                excerpt=excerpt,
                score=r.get("tally", {}).get("total") if isinstance(r.get("tally"), dict) else None,
                raw=r,
            )
        )
    return out


# Parse a Scite publication date that may be a bare year (int/str), an ISO
# date, or None. Returns a datetime (Jan 1 for bare years) or None.
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

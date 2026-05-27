"""Brave News Search adapter (W2 — replaces Exa as the primary source).

One function — search() — that GETs Brave's News Search API and returns the
same ExaResult list the rest of the pipeline already consumes, so swapping
providers is a one-line change in pipeline._make_provider. Mirrors
exa_client: a load_response_file() reads a saved JSON dump for offline replay.

Auth: an `X-Subscription-Token` header with the Brave Search API key. Get one
at https://brave.com/search/api/ — set BRAVE_SEARCH_API_KEY in .env. Until then
the pipeline falls back to Exa or a --from-file fixture, so nothing here is
exercised live without the key.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .exa_client import ExaResult

# Brave News Search endpoint. The web-search endpoint has the same auth shape
# if a broader query surface is wanted later.
BRAVE_NEWS_SEARCH = "https://api.search.brave.com/res/v1/news/search"


# Exception for any non-200 / unparseable Brave response. Caught by the
# pipeline (alongside ExaError) so a failed query records an error poll row
# and the cycle continues.
class BraveError(RuntimeError):
    pass


# Execute one Brave News Search and return parsed ExaResults. start/end dates
# map to Brave's `freshness` range so repeated polls don't re-surface old
# articles. Signature matches exa_client.search so providers are swappable.
def search(
    query: str,
    *,
    api_key: str,
    num_results: int = 5,
    start_published_date: datetime | None = None,
    end_published_date: datetime | None = None,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """One Brave News Search call → list[ExaResult]."""
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        params: dict[str, Any] = {
            "q": query,
            "count": min(max(num_results, 1), 50),  # Brave caps count at 50
            "spellcheck": 0,
        }
        freshness = _freshness(start_published_date, end_published_date)
        if freshness:
            params["freshness"] = freshness
        resp = client.get(
            BRAVE_NEWS_SEARCH,
            params=params,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
        )
        if resp.status_code != 200:
            raise BraveError(f"Brave news search failed: {resp.status_code} {resp.text[:300]}")
        return _parse_response(resp.json())
    finally:
        if owns:
            client.close()


# Load a saved Brave response JSON from disk — offline replay, same return
# type as the live call.
def load_response_file(path: Path) -> list[ExaResult]:
    return _parse_response(json.loads(path.read_text()))


# Map a start/end window to Brave's `freshness` parameter. A precise date
# range (`YYYY-MM-DDtoYYYY-MM-DD`) when a start is given; else None (all time).
def _freshness(start: datetime | None, end: datetime | None) -> str | None:
    if start is None:
        return None
    end = end or datetime.now(start.tzinfo)
    return f"{start:%Y-%m-%d}to{end:%Y-%m-%d}"


# Parse a Brave News response body into ExaResults. Brave returns publisher
# article URLs (so source_tiers still classifies them correctly); page_age is
# an ISO timestamp when present, otherwise the date is left None.
def _parse_response(body: dict[str, Any]) -> list[ExaResult]:
    out: list[ExaResult] = []
    for r in body.get("results", []):
        out.append(
            ExaResult(
                title=(r.get("title") or "").strip(),
                url=(r.get("url") or "").strip(),
                published_at=_parse_dt(r.get("page_age") or r.get("age")),
                excerpt=(r.get("description") or r.get("snippet") or "").strip(),
                score=None,  # Brave doesn't return a relevance score
                raw=r,
            )
        )
    return out


# Parse Brave's `page_age` ISO-8601 timestamp; returns None for the human
# `age` strings ("3 hours ago") or anything unparseable.
def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

"""Brave Web Search adapter for source discovery tasks.

The news pipeline uses Brave News Search for articles. IR URL discovery needs
ordinary web results because canonical investor-relations pages are often not
news articles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

BRAVE_WEB_SEARCH = "https://api.search.brave.com/res/v1/web/search"


class BraveWebError(RuntimeError):
    pass


@dataclass(frozen=True)
class BraveWebResult:
    title: str
    url: str
    description: str
    raw: dict[str, Any]


def search(
    query: str,
    *,
    api_key: str,
    num_results: int = 10,
    client: httpx.Client | None = None,
) -> list[BraveWebResult]:
    owns = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        resp = client.get(
            BRAVE_WEB_SEARCH,
            params={"q": query, "count": min(max(num_results, 1), 20), "spellcheck": 0},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
        )
        if resp.status_code != 200:
            raise BraveWebError(f"Brave web search failed: {resp.status_code} {resp.text[:240]}")
        return _parse_response(resp.json())
    finally:
        if owns:
            client.close()


def _parse_response(body: dict[str, Any]) -> list[BraveWebResult]:
    out: list[BraveWebResult] = []
    for row in (body.get("web") or {}).get("results", []):
        url = (row.get("url") or "").strip()
        title = (row.get("title") or "").strip()
        if not url or not title:
            continue
        out.append(BraveWebResult(
            title=title,
            url=url,
            description=(row.get("description") or row.get("snippet") or "").strip(),
            raw=row,
        ))
    return out

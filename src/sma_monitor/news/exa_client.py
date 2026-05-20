"""Thin Exa search adapter.

One function — search() — that POSTs to /search and returns parsed results.
Mirrors flex.py's --from-file pattern: load_response_file() reads a saved
JSON dump so the pipeline can be exercised offline (tests, demos, replays).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

EXA_BASE = "https://api.exa.ai"
EXA_SEARCH = f"{EXA_BASE}/search"


class ExaError(RuntimeError):
    pass


@dataclass
class ExaResult:
    title: str
    url: str
    published_at: datetime | None
    excerpt: str
    score: float | None
    raw: dict[str, Any]


def search(
    query: str,
    *,
    api_key: str,
    num_results: int = 5,
    start_published_date: datetime | None = None,
    end_published_date: datetime | None = None,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """One Exa search call."""
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "type": "auto",
            "contents": {"text": {"maxCharacters": 1000}},
        }
        if start_published_date:
            payload["startPublishedDate"] = _iso_z(start_published_date)
        if end_published_date:
            payload["endPublishedDate"] = _iso_z(end_published_date)
        resp = client.post(
            EXA_SEARCH,
            json=payload,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise ExaError(f"Exa search failed: {resp.status_code} {resp.text[:300]}")
        return _parse_response(resp.json())
    finally:
        if owns:
            client.close()


def load_response_file(path: Path) -> list[ExaResult]:
    return _parse_response(json.loads(path.read_text()))


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_response(body: dict[str, Any]) -> list[ExaResult]:
    out: list[ExaResult] = []
    for r in body.get("results", []):
        out.append(
            ExaResult(
                title=(r.get("title") or "").strip(),
                url=(r.get("url") or "").strip(),
                published_at=_parse_dt(r.get("publishedDate") or r.get("published_date")),
                excerpt=(r.get("text") or r.get("excerpt") or "").strip(),
                score=r.get("score"),
                raw=r,
            )
        )
    return out


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

"""Perplexity Search API wrapper used as a model-callable web_search tool."""
from __future__ import annotations

import os

import httpx

# Perplexity Search API endpoint.
SEARCH_URL = "https://api.perplexity.ai/search"

# OpenAI-style tool schema advertised to the chat model.
TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the live web for current news, filings, trial data, FDA events, "
            "catalyst dates, or prices. Returns titles, URLs, dates and snippets."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
}


# True when a Perplexity API key is configured.
def web_search_available() -> bool:
    return bool((os.environ.get("PERPLEXITY_API_KEY") or "").strip())


# Run one search and return a compact text block for the model (errors returned as text).
def run_web_search(query: str, max_results: int = 6) -> str:
    key = (os.environ.get("PERPLEXITY_API_KEY") or "").strip()
    if not key:
        return "web search unavailable: PERPLEXITY_API_KEY not set"
    try:
        resp = httpx.post(
            SEARCH_URL,
            json={"query": query, "max_results": max_results},
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except (httpx.HTTPError, ValueError) as e:
        return f"web search failed: {e}"
    if not results:
        return "no results"
    out = []
    for r in results:
        snippet = (r.get("snippet") or "")[:900]
        out.append(
            f"- {r.get('title')} ({r.get('url')}) [{r.get('last_updated') or r.get('date') or 'n/d'}]\n"
            f"  {snippet}"
        )
    return "\n".join(out)

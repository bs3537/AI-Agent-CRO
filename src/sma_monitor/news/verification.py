"""Native-search verification handoff for API-derived data.

The source policy requires that FMP/Semantic Scholar API data be corroborated
before Codex treats it as reliable. Python no longer calls Brave Search API;
instead this module returns an explicit low-confidence handoff marker so Codex
GPT-5.5 can perform native web-search corroboration inside the LLM run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .exa_client import ExaResult
from .source_tiers import source_tier

# Web results at this tier or better count as corroboration for offline/unit
# classification helpers. Tier 6 and empty results do not count.
DEFAULT_MIN_TIER = 5
NATIVE_SEARCH_PROVIDER = "codex_native_web_search"


# One API datum's verification result and any sources supplied by tests/fixtures.
@dataclass
class Verification:
    subject: str
    corroborated: bool
    sources: list[ExaResult] = field(default_factory=list)
    provider: str = NATIVE_SEARCH_PROVIDER


# Rank supplied web results most-credible-first and decide corroboration.
def _classify(subject: str, results: list[ExaResult], *, min_tier: int) -> Verification:
    ranked = sorted(results, key=lambda r: source_tier(r.url))
    credible = [r for r in ranked if source_tier(r.url) <= min_tier]
    return Verification(subject=subject, corroborated=bool(credible), sources=ranked[:5])


# Return a native-search handoff instead of making a Python Brave REST call.
def verify(
    subject: str,
    *,
    api_key: str | None = None,
    num_results: int = 3,
    min_tier: int = DEFAULT_MIN_TIER,
    client: httpx.Client | None = None,
) -> Verification:
    _ = (api_key, num_results, min_tier, client)
    return Verification(subject=subject, corroborated=False)


# Mark an FMP financial snapshot for Codex native web-search corroboration.
def verify_fmp(company: str, *, api_key: str | None = None, **kw) -> Verification:
    return verify(f"{company} quarterly financial results earnings", api_key=api_key, **kw)


# Mark a Semantic Scholar paper for Codex native web-search corroboration.
def verify_literature(title: str, *, api_key: str | None = None, **kw) -> Verification:
    return verify(title, api_key=api_key, **kw)

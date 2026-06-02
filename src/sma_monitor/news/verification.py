"""Brave-verification layer for API-derived data.

The source policy requires that data from external APIs (FMP financials,
Semantic Scholar literature — see source_policy.API_SOURCES_REQUIRING_VERIFICATION)
be corroborated by an independent web search before the LLM trusts it. This module
runs that cross-check via Brave and returns the corroborating sources WITH their
credibility tiers, so the LLM can weigh them under the decision prompt's rule
(uncorroborated API data is low-confidence and must not escalate a verdict).

This is a corroboration SIGNAL, not a numeric fact-check: a keyword search can't
confirm a specific figure, so the boolean only means "an independent, non-junk
source was found," and the real judgment is the LLM's, informed by the attached
sources. Reuses brave_client (search) and source_tiers (credibility).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from . import brave_client
from .exa_client import ExaResult
from .source_tiers import source_tier

# Web results at this tier or better count as corroboration. Tier 6 (blogs,
# forums, aggregators) and empty results do not — see source_tiers._TIERS.
DEFAULT_MIN_TIER = 5


# One API datum's verification result: whether an independent web source
# corroborates it, plus the top sources (most-credible first) for Codex to weigh.
@dataclass
class Verification:
    subject: str
    corroborated: bool
    sources: list[ExaResult] = field(default_factory=list)
    provider: str = "brave"


# Rank web results most-credible-first and decide corroboration: True when at
# least one result is at min_tier or better. Pure (no I/O) so it's unit-tested
# directly; verify() is the thin Brave-backed wrapper.
def _classify(subject: str, results: list[ExaResult], *, min_tier: int) -> Verification:
    ranked = sorted(results, key=lambda r: source_tier(r.url))
    credible = [r for r in ranked if source_tier(r.url) <= min_tier]
    return Verification(subject=subject, corroborated=bool(credible), sources=ranked[:5])


# Cross-check a subject string against the web via Brave and classify the result.
# Degrades gracefully (corroborated=False, no sources) when no Brave key is set,
# so a missing key never crashes the due-diligence cycle.
def verify(
    subject: str,
    *,
    api_key: str | None,
    num_results: int = 3,
    min_tier: int = DEFAULT_MIN_TIER,
    client: httpx.Client | None = None,
) -> Verification:
    if not api_key:
        return Verification(subject=subject, corroborated=False)
    results = brave_client.search(
        subject, api_key=api_key, num_results=num_results, client=client
    )
    return _classify(subject, results, min_tier=min_tier)


# Corroborate an FMP financial snapshot: search recent independent coverage of
# the company's financials so the LLM can compare it against the FMP figures.
def verify_fmp(company: str, *, api_key: str | None, **kw) -> Verification:
    return verify(f"{company} quarterly financial results earnings", api_key=api_key, **kw)


# Corroborate a Semantic Scholar paper by searching its title on the web — a real
# paper surfaces in independent (publisher/PubMed/DOI) sources.
def verify_literature(title: str, *, api_key: str | None, **kw) -> Verification:
    return verify(title, api_key=api_key, **kw)

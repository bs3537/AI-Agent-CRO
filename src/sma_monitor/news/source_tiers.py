"""Map URL → source priority tier.

Per PLAN.MD §2C source hierarchy:
  1 = company-issued (SEC, IR wires)
  2 = regulatory (FDA, EMA, PMDA, clinicaltrials.gov)
  3 = peer-reviewed
  4 = trade press
  5 = general financial press   (default if unknown)
  6 = aggregators / retail commentary

Phase 5 alert formatter gates on source_tier — tier-6 alone won't fire a
Tier-A alert; needs corroboration from a higher tier.
"""
from __future__ import annotations

from urllib.parse import urlparse

# (tier, host fragments) pairs evaluated top-down. First matching tier wins.
# Adding a new source: append to the existing tier's list, do not invent
# tiers between the canonical 1–6.
_TIERS: list[tuple[int, list[str]]] = [
    (1, ["sec.gov", "businesswire.com", "prnewswire.com", "globenewswire.com",
         "ir.", "investors."]),
    (2, ["fda.gov", "ema.europa.eu", "pmda.go.jp", "clinicaltrials.gov", "who.int"]),
    (3, ["nejm.org", "jamanetwork.com", "thelancet.com", "ascopubs.org",
         "ashpublications.org", "nature.com", "science.org", "cell.com",
         "bmj.com", "annals.org",
         # W2: Scite literature links resolve via DOI / PubMed → peer-reviewed.
         "doi.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "scite.ai"]),
    (4, ["fiercebiotech.com", "fiercepharma.com", "biopharmadive.com", "endpts.com",
         "statnews.com", "biospace.com", "biopharmacatalyst.com"]),
    (5, ["reuters.com", "bloomberg.com", "wsj.com", "ft.com", "marketwatch.com",
         "cnbc.com", "barrons.com", "forbes.com", "nytimes.com", "axios.com"]),
    (6, ["seekingalpha.com", "fool.com", "benzinga.com", "yahoo.com", "msn.com",
         "investorshub.advfn.com"]),
]

# Tier assigned when the host doesn't match any known matcher.
DEFAULT_TIER = 5


# Return the source tier (1..6) for the given URL. Falls back to DEFAULT_TIER
# (5 = general press) when no matcher fires.
def source_tier(url: str) -> int:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return DEFAULT_TIER
    host = host.removeprefix("www.")
    for tier, matchers in _TIERS:
        for m in matchers:
            if m in host or host.endswith(m):
                return tier
    return DEFAULT_TIER


# Return a short, human-readable source label (the URL's host, stripped of
# `www.`). Used in alert and digest renderings.
def source_label(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return "unknown"

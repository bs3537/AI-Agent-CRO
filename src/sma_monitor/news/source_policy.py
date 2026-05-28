"""Due-diligence source policy — precedence + verification.

Single source of truth for the manager's mandated source ordering in Codex's
daily per-holding due diligence. Codex does not fetch sources itself: the
pipeline assembles source-tagged, Brave-verified evidence and Codex judges it
under these rules (see decision/prompt.build_system_prompt). Both the ingestion
pipeline (fetch order) and the decision prompt (evidence weighting) read from
here so the precedence is defined once.

Precedence (most-authoritative first):
  - Financials:            SEC filings  -> FMP
  - Biomed literature:     PubMed / ClinicalTrials.gov / web search -> Semantic Scholar
  - Non-biomed literature: web search (Brave) -> Semantic Scholar
Verification:
  - Any datum from an external API (FMP, Semantic Scholar) must be corroborated
    by a Brave web search before it is treated as reliable; uncorroborated API
    data is surfaced to Codex as low-confidence and never escalates a verdict.
"""
from __future__ import annotations

from ..portfolio.schema import Holding, Sidecar

# Ordered source precedence per evidence domain (most-authoritative first).
# Stable identifiers reused as evidence tags and named in the decision prompt.
FINANCIAL_SOURCE_ORDER: tuple[str, ...] = ("sec_filings", "fmp")
BIOMED_LITERATURE_ORDER: tuple[str, ...] = (
    "pubmed",
    "clinicaltrials_gov",
    "web_search",
    "semantic_scholar",
)
GENERAL_LITERATURE_ORDER: tuple[str, ...] = ("web_search", "semantic_scholar")

# External-API sources whose data must be corroborated by a Brave web search
# before Codex treats it as reliable (the verification mandate).
API_SOURCES_REQUIRING_VERIFICATION: tuple[str, ...] = ("fmp", "semantic_scholar")

# The web-search provider used both as a primary literature source and as the
# cross-check that verifies API-derived data.
VERIFICATION_PROVIDER = "brave"


# A holding is biomed when its sidecar names drug indications — the clean signal
# that selects the PubMed/ClinicalTrials.gov literature path over the general
# web-search path. Stage is unreliable here: it's a required biotech-only
# Literal, so even a non-biotech name carries a clinical/commercial value.
def is_biomed(h: Holding | Sidecar) -> bool:
    return bool(getattr(h, "indications", None))


# Ordered literature source precedence for a holding (biomed vs general), so the
# pipeline and the decision prompt agree on which primaries apply to the name.
def literature_order(h: Holding | Sidecar) -> tuple[str, ...]:
    return BIOMED_LITERATURE_ORDER if is_biomed(h) else GENERAL_LITERATURE_ORDER

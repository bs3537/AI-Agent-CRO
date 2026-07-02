"""Due-diligence source policy — precedence + verification.

Single source of truth for the manager's mandated source ordering in Codex's
daily per-holding due diligence. The pipeline assembles source-tagged direct
evidence, and Codex GPT-5.5 uses native web search for fresh corroboration when
the prompt requests it (see decision/prompt.build_system_prompt).

Precedence (most-authoritative first):
  - Financials:            SEC filings  -> FMP
  - Biomed literature:     PubMed / ClinicalTrials.gov -> Semantic Scholar
  - Non-biomed literature: Semantic Scholar plus Codex native web-search checks
Verification:
  - Any datum from an external API (FMP, Semantic Scholar) should be corroborated
    by Codex native web search or a primary source before it is treated as
    reliable; uncorroborated API data is surfaced as low-confidence.
"""
from __future__ import annotations

from ..portfolio.schema import Holding, Sidecar

# Ordered source precedence per evidence domain (most-authoritative first).
# Stable identifiers reused as evidence tags and named in the decision prompt.
FINANCIAL_SOURCE_ORDER: tuple[str, ...] = ("sec_filings", "fmp")
BIOMED_LITERATURE_ORDER: tuple[str, ...] = (
    "pubmed",
    "clinicaltrials_gov",
    "semantic_scholar",
)
GENERAL_LITERATURE_ORDER: tuple[str, ...] = ("semantic_scholar",)

# External-API sources whose data should be corroborated by Codex native web
# search or a primary source before Codex treats it as reliable.
API_SOURCES_REQUIRING_VERIFICATION: tuple[str, ...] = ("fmp", "semantic_scholar")

# The web-search provider used for cross-check instructions in prompts.
VERIFICATION_PROVIDER = "codex_native_web_search"


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

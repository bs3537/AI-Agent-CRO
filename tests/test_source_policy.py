"""Source-policy tests — precedence ordering, biomed detection, prompt wiring.

Verifies the single-source-of-truth precedence (financials SEC->FMP, biomed vs
general literature), the API-verification set, and that the decision system
prompt actually carries the source policy Codex must follow.
"""
from __future__ import annotations

from sma_monitor.news import source_policy as sp
from sma_monitor.portfolio.schema import Sidecar


# Financials precede SEC->FMP; literature branches PubMed-first vs web-first;
# the verification set is exactly the two external APIs (FMP + Semantic Scholar).
def test_precedence_constants():
    assert sp.FINANCIAL_SOURCE_ORDER == ("sec_filings", "fmp")
    assert sp.BIOMED_LITERATURE_ORDER[0] == "pubmed"
    assert sp.BIOMED_LITERATURE_ORDER[-1] == "semantic_scholar"
    assert sp.GENERAL_LITERATURE_ORDER == ("web_search", "semantic_scholar")
    assert set(sp.API_SOURCES_REQUIRING_VERIFICATION) == {"fmp", "semantic_scholar"}


# A holding with named drug indications is biomed; one without is general.
def test_is_biomed_keys_on_indications():
    bio = Sidecar(ticker="VRTX", conviction_tier=5, stage="commercial_stage",
                  thesis="x", indications=["cystic fibrosis"])
    gen = Sidecar(ticker="KTOS", conviction_tier=3, stage="commercial_stage",
                  thesis="x")
    assert sp.is_biomed(bio) is True
    assert sp.is_biomed(gen) is False


# Literature precedence follows the biomed branch: PubMed-first vs web-first.
def test_literature_order_branches():
    bio = Sidecar(ticker="VRTX", conviction_tier=5, stage="commercial_stage",
                  thesis="x", indications=["sickle cell disease"])
    gen = Sidecar(ticker="KTOS", conviction_tier=3, stage="commercial_stage",
                  thesis="x")
    assert sp.literature_order(bio) == sp.BIOMED_LITERATURE_ORDER
    assert sp.literature_order(gen) == sp.GENERAL_LITERATURE_ORDER


# The decision system prompt carries the source policy Codex must follow.
def test_system_prompt_carries_source_policy():
    from sma_monitor.decision.prompt import build_system_prompt
    text = build_system_prompt()
    assert "SEC filings" in text
    assert "Semantic Scholar" in text
    assert "corroborat" in text.lower()  # corroborated / corroboration


# The literature poll queries sources in source_policy order, including only
# those whose key is available (PubMed/ClinicalTrials.gov are keyless primaries).
def test_literature_sources_honor_policy_order():
    from sma_monitor.news.pipeline import _literature_sources
    bio = Sidecar(ticker="VRTX", conviction_tier=5, stage="commercial_stage",
                  thesis="x", indications=["cystic fibrosis"])
    gen = Sidecar(ticker="KTOS", conviction_tier=3, stage="commercial_stage", thesis="x")
    names = lambda srcs: [n for n, _ in srcs]  # noqa: E731
    assert names(_literature_sources(bio, s2_key="k", brave_key="b", ncbi_key=None,
                                     fixture=None)) == \
        ["pubmed", "clinicaltrials_gov", "web_search", "semantic_scholar"]
    assert names(_literature_sources(gen, s2_key="k", brave_key="b", ncbi_key=None,
                                     fixture=None)) == ["web_search", "semantic_scholar"]
    # Missing keys drop the keyed sources; keyless primaries remain.
    assert names(_literature_sources(bio, s2_key=None, brave_key=None, ncbi_key=None,
                                     fixture=None)) == ["pubmed", "clinicaltrials_gov"]

"""Brave-verification tests — corroboration classification + graceful degrade.

Tests the pure classifier (_classify) against canned results so no network is
touched, plus the no-key degrade path. The live Brave cross-check is exercised
by smoke tests, not the suite.
"""
from __future__ import annotations

from sma_monitor.news.exa_client import ExaResult
from sma_monitor.news.verification import _classify, verify


# Build a minimal ExaResult carrying just the URL the classifier tiers on.
def _r(url: str) -> ExaResult:
    return ExaResult(title="t", url=url, published_at=None, excerpt="", score=None, raw={})


# A recognized credible source (tier-3 peer-reviewed) corroborates; junk alone
# does not — and results are surfaced most-credible-first regardless.
def test_classify_corroboration():
    v = _classify("subj", [_r("https://seekingalpha.com/x"), _r("https://www.nejm.org/y")],
                  min_tier=5)
    assert v.corroborated is True
    assert v.sources[0].url.startswith("https://www.nejm.org")  # most-credible first


# Only tier-6 junk → not corroborated, but the source is still surfaced for Codex.
def test_classify_junk_only():
    v = _classify("subj", [_r("https://seekingalpha.com/x")], min_tier=5)
    assert v.corroborated is False
    assert len(v.sources) == 1


# No web results → not corroborated, empty sources.
def test_classify_empty():
    assert _classify("subj", [], min_tier=5).corroborated is False


# With no Brave key, verify degrades to uncorroborated rather than crashing.
def test_verify_no_key_degrades():
    v = verify("anything", api_key=None)
    assert v.corroborated is False and v.sources == []

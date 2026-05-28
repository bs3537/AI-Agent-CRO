"""Decision-prompt tests — FMP corroboration rendering in the user message.

Verifies the Brave cross-check (6c) is surfaced to Codex: a corroborated FMP
snapshot shows its status + backing source, and an unchecked one says so (so
Codex applies the source-policy low-confidence rule).
"""
from __future__ import annotations

from sma_monitor.decision.prompt import build_user_message
from sma_monitor.decision.schema import DecisionCandidate


# Build a minimal DecisionCandidate, overriding only the fields under test.
def _candidate(**kw) -> DecisionCandidate:
    base = dict(
        ticker="AQST", company_name="Aquestive", stage="commercial_stage",
        conviction_tier=3, thesis="durable franchise", pct_nav=0.18,
        market_value=1000.0, cost_basis=None, open_pnl=None, pnl_pct=None,
        nearest_catalyst_days=None, has_overdue_catalyst=False,
    )
    base.update(kw)
    return DecisionCandidate(**base)


# A corroborated FMP snapshot renders its status + the backing source for Codex.
def test_prompt_renders_fmp_corroboration():
    c = _candidate(
        fmp_metrics={"company": "Aquestive", "current_ratio": 2.0},
        fmp_corroboration={
            "corroborated": True,
            "sources": [{"title": "Q1 results", "url": "https://www.globenewswire.com/x", "tier": 1}],
        },
    )
    msg = build_user_message(c)
    assert "corroborated" in msg.lower()
    assert "globenewswire" in msg


# Without a corroboration check, the prompt says so (Codex treats FMP unverified).
def test_prompt_marks_unchecked_fmp():
    msg = build_user_message(_candidate(fmp_metrics={"company": "X"}))
    assert "not checked" in msg.lower()

"""Decision-prompt tests — FMP corroboration rendering in the user message.

Verifies independent corroboration is surfaced to Codex: a corroborated FMP
snapshot shows its status + backing source, and an unchecked one asks Codex to
use native web search before treating API data as reliable.
"""
from __future__ import annotations

from sma_monitor.decision.prompt import build_system_prompt, build_user_message
from sma_monitor.decision.schema import DecisionCandidate, TechnicalSnapshot


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
            "sources": [
                {"title": "Q1 results", "url": "https://www.globenewswire.com/x", "tier": 1}
            ],
        },
    )
    msg = build_user_message(c)
    assert "corroborated" in msg.lower()
    assert "globenewswire" in msg


# Without a Python-side corroboration check, the prompt tells Codex to use native web search.
def test_prompt_marks_unchecked_fmp_for_codex_native_search():
    msg = build_user_message(_candidate(fmp_metrics={"company": "X"}))
    assert "not checked" in msg.lower()
    assert "native web search" in msg.lower()
    assert "brave" not in msg.lower()


def test_prompt_renders_ema20_snapshot():
    msg = build_user_message(
        _candidate(
            technical=TechnicalSnapshot(
                latest_close=95.0,
                latest_ema20=100.0,
                price_vs_ema20_pct=-0.05,
                consecutive_below_ema20=4,
                ema20_slope_5d=-0.02,
                technical_state="extended_below_ema20",
                risk_points=15,
            )
        )
    )
    assert "TECHNICAL TREND" in msg
    assert "extended_below_ema20" in msg
    assert "consecutive closes below EMA20: 4" in msg


def test_prompt_marks_ten_percent_pnl_loss_warning():
    msg = build_user_message(
        _candidate(open_pnl=-125.0, pnl_pct=-0.125, cost_basis=1000.0)
    )
    assert "P&L warning" in msg
    assert "10% loss threshold" in msg


def test_prompt_marks_auto_scaffold_as_fallback():
    msg = build_user_message(
        _candidate(thesis="STUB - auto-scaffolded 2026-05-29; replace with actual thesis.")
    )
    assert "THESIS AUTHORITY" in msg
    assert "auto_scaffold_fallback" in msg
    assert "provisional working thesis" in msg


def test_prompt_marks_manager_thesis_as_controlling():
    msg = build_user_message(
        _candidate(
            thesis="My thesis: launch execution and cash runway are the controlling variables.",
            thesis_doc_text="Older report: background biology and prior catalyst notes.",
        )
    )
    assert "manager_entered_primary" in msg
    assert "Manager thesis controls" in msg
    assert "SUPPORTING THESIS DOCUMENTS" in msg


def test_monitoring_prompts_use_the_multi_sector_portfolio_mandate():
    from sma_monitor.red_team.prompt import _VOICE
    from sma_monitor.scorer.prompt import SYSTEM_PROMPT

    prompts = [build_system_prompt(), SYSTEM_PROMPT, _VOICE]
    for prompt in prompts:
        assert "multi-sector" in prompt
        assert "biotech-heavy" not in prompt

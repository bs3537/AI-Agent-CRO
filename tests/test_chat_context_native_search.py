"""Chat-context tests for Brave-free Codex native web search handoff."""
from __future__ import annotations

from datetime import UTC, datetime
from sma_monitor.chat.context import _live_web_context
from sma_monitor.portfolio.schema import Holding


# Build a minimal holding for live-search context behavior tests.
def _holding() -> Holding:
    return Holding(
        ticker="VRTX",
        company_name="Vertex",
        qty=1,
        market_value=1.0,
        pct_nav=0.01,
        cost_basis=None,
        pulled_at=datetime(2035, 1, 1, tzinfo=UTC),
        nav=100.0,
        thesis="test",
        stage="commercial_stage",
        conviction_tier=3,
        indications=["sickle cell disease"],
        catalysts=[],
    )


# Time-sensitive chat questions should instruct the Codex runner to use native
# web search instead of calling a Brave REST client in Python.
def test_live_web_context_uses_codex_native_search_handoff():
    ctx = _live_web_context("What is the latest FDA catalyst news for VRTX?", [_holding()])

    assert ctx["status"] == "codex_native_web_search_requested"
    assert ctx["searched_at"] is not None
    assert "CODEX NATIVE WEB SEARCH REQUEST" in ctx["text"]
    assert "VRTX Vertex" in ctx["text"]
    assert "Brave" not in ctx["text"]

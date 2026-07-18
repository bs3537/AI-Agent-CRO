"""Regression checks for the dense table dashboard source contract."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"
TABLE = ROOT / "frontend" / "src" / "components" / "PositionsTable.tsx"
KPI = ROOT / "frontend" / "src" / "components" / "KpiStrip.tsx"
TRIAGE = ROOT / "frontend" / "src" / "components" / "TriageBands.tsx"


def test_table_view_is_default_and_persistent():
    source = APP.read_text()

    assert "ai-cro-dashboard-view-v1" in source
    assert "return 'table'" in source
    assert "<PositionsTable" in source
    assert "<KpiStrip" in source
    assert "<TriageBands" in source


def test_table_uses_current_fmp_target_contract_and_skips_etfs():
    source = TABLE.read_text()

    assert "pos.analyst_target" in source
    assert "pos.is_etf" in source
    assert "mean_price_target" in source
    assert "upside_pct" in source
    assert "price_as_of" in source
    assert "pt_consensus" not in source
    assert "pt_upside_pct" not in source


def test_table_preserves_live_quote_and_sourced_catalyst_behavior():
    table = TABLE.read_text()
    kpi = KPI.read_text()

    assert "liveQuotes" in table
    assert "quote.price * pos.qty" in table
    assert "item.source_url" in table
    assert "item.source_title" in table
    assert "liveQuotes" in kpi


def test_triage_rules_cover_broken_sell_watch_and_overdue():
    source = TRIAGE.read_text()

    assert "attentionState === 'broken'" in source
    assert "grade === 'D'" in source
    assert "pos.has_overdue_catalyst" in source
    assert "attentionState === 'watch'" in source
    assert "extended_below_ema20" in source

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



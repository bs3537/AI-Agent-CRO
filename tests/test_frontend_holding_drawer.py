"""Regression checks for the coordinated dashboard drawers."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"
HOLDING = ROOT / "frontend" / "src" / "components" / "HoldingDrawer.tsx"
THESIS = ROOT / "frontend" / "src" / "components" / "ThesisDrawer.tsx"


def test_holding_drawer_preserves_newer_target_thesis_and_live_features():
    source = HOLDING.read_text()

    assert "liveQuote.price * position.qty" in source
    assert "!position.is_etf" in source
    assert "<PriceTargetMetric" in source
    assert "PRELIMINARY THESIS" in source
    assert "<ThesisTargetLine" in source
    assert "detail.catalyst_outlook" in source
    assert "item.source_url" in source
    assert "item.source_title" in source



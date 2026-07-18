"""Regression checks for the coordinated dashboard drawers."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"
HOLDING = ROOT / "frontend" / "src" / "components" / "HoldingDrawer.tsx"
THESIS = ROOT / "frontend" / "src" / "components" / "ThesisDrawer.tsx"


def test_app_uses_merged_holding_drawer_with_current_callbacks():
    source = APP.read_text()

    assert "import HoldingDrawer" in source
    assert "<HoldingDrawer" in source
    assert "liveQuote={" in source
    assert "onUpload={onUpload}" in source
    assert "onRecompute={onRecompute}" in source
    assert "onDelete={onDeleteHolding}" in source
    assert "DetailDrawer" not in source


def test_holding_and_thesis_drawers_are_coordinated_and_responsive():
    holding = HOLDING.read_text()
    thesis = THESIS.read_text()

    assert 'variant="persistent"' in holding
    assert 'variant="persistent"' in thesis
    assert "thesisOpen ? `${THESIS_WIDTH}px` : 0" in holding
    assert "width: { xs: '100vw', sm: DETAIL_WIDTH }" in holding
    assert "width: { xs: '100vw', sm: 520 }" in thesis


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


def test_holding_drawer_refreshes_detail_after_mutations():
    source = HOLDING.read_text()

    assert "await onRecompute(ticker)" in source
    assert "await onUpload(ticker, file)" in source
    assert "setDetail(await api.detail(ticker))" in source
    assert "await onDelete(ticker)" in source

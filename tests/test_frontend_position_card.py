from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSITION_CARD = ROOT / "frontend" / "src" / "components" / "PositionCard.tsx"
PRICE_TARGET_METRIC = ROOT / "frontend" / "src" / "components" / "PriceTargetMetric.tsx"
THESIS_TARGET_LINE = ROOT / "frontend" / "src" / "components" / "ThesisTargetLine.tsx"


def test_position_card_renders_current_thesis_preview():
    """The dashboard tile should visibly reflect a saved PM thesis from the grid payload."""
    source = POSITION_CARD.read_text(encoding="utf-8")

    assert "Current thesis" in source
    assert "thesisPreview" in source
    assert "thesisPreview || 'No thesis saved.'" in source
    assert "'Preliminary thesis' : 'Current thesis'" in source
    assert "PreliminaryThesisPreview" in source


def test_position_card_renders_tipranks_price_target_metric():
    """Each position tile should show the compact TipRanks target metric."""
    card_source = POSITION_CARD.read_text(encoding="utf-8")
    metric_source = PRICE_TARGET_METRIC.read_text(encoding="utf-8")
    thesis_target_source = THESIS_TARGET_LINE.read_text(encoding="utf-8")

    assert "!pos.is_etf && <PriceTargetMetric target={pos.analyst_target} />" in card_source
    assert "<ThesisTargetLine target={pos.analyst_target} isEtf={pos.is_etf} />" in card_source
    assert "PRICE TARGET · TIPRANKS" in metric_source
    assert "upside_pct" in metric_source
    assert "WarningAmberIcon" in metric_source
    assert "Price target" in thesis_target_source
    assert "TipRanks mean" in thesis_target_source

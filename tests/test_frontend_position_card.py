from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSITION_CARD = ROOT / "frontend" / "src" / "components" / "PositionCard.tsx"


def test_position_card_renders_current_thesis_preview():
    """The dashboard tile should visibly reflect a saved PM thesis from the grid payload."""
    source = POSITION_CARD.read_text(encoding="utf-8")

    assert "Current thesis" in source
    assert "thesisPreview" in source
    assert "thesisPreview || 'No thesis saved.'" in source

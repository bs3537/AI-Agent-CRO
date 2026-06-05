from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"


def test_app_surfaces_queued_recompute_notice_for_dashboard_role():
    source = APP.read_text()

    assert "const recompute = await api.recompute(ticker, true)" in source
    assert "recompute.scheduled" in source
    assert "recompute queued" in source

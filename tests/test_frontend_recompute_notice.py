from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"
API = ROOT / "frontend" / "src" / "api.ts"
TYPES = ROOT / "frontend" / "src" / "types.ts"


def test_app_surfaces_queued_recompute_notice_for_dashboard_role():
    source = APP.read_text()

    assert "const recompute = await api.recompute(ticker, true)" in source
    assert "recompute.scheduled" in source
    assert "recompute queued" in source


def test_frontend_polls_dashboard_queued_recompute_before_refreshing_tile():
    app = APP.read_text()
    api = API.read_text()
    types = TYPES.read_text()

    assert "waitForQueuedRecompute" in app
    assert "api.recomputeStatus(requestId)" in app
    assert "await waitForQueuedRecompute(recompute.request_id)" in app
    assert "recomputeStatus: (requestId: string)" in api
    assert "fetch(`/api/positions/recompute/${requestId}`)" in api
    assert "export interface RecomputeStatusResponse" in types

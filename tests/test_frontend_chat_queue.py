from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_chat_panel_polls_vps_runner_for_queued_chat():
    panel = (REPO / "frontend/src/components/ChatPanel.tsx").read_text()
    api = (REPO / "frontend/src/api.ts").read_text()
    types = (REPO / "frontend/src/types.ts").read_text()

    assert "ChatQueuedResponse" in types
    assert "ChatStatusResponse" in types
    assert "ChatSubmitResponse" in types
    assert "chatStatus" in api
    assert "/api/chat/${requestId}" in api
    assert "isQueuedChatResponse" in panel
    assert "waitForQueuedChat" in panel
    assert "api.chatStatus" in panel
    assert "sleep(1500)" in panel
    assert "VPS Codex" in panel

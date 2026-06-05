from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sma_monitor.orchestrator import pipeline


class _SettingsWithFlexCreds:
    ibkr_flex_token = "token"
    ibkr_flex_query_id = "query"
    anthropic_api_key = None

    def missing_for(self, phase: int) -> list[str]:
        return []


def _patch_successful_position_refresh(monkeypatch, *, attempts_before_success: int = 1):
    attempts: list[int] = []
    pulled_at = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)

    def fake_fetch_statement(*, token: str, query_id: str):
        attempts.append(1)
        if len(attempts) < attempts_before_success:
            raise pipeline.FlexError("temporary IBKR/Flex failure")
        return SimpleNamespace(xml="<FlexQueryResponse />", pulled_at=pulled_at)

    monkeypatch.setattr(pipeline, "settings", _SettingsWithFlexCreds())
    monkeypatch.setattr(pipeline, "latest_pull_at", lambda: None)
    monkeypatch.setattr(pipeline, "fetch_statement", fake_fetch_statement)
    monkeypatch.setattr(pipeline, "parse_positions", lambda xml, pulled_at: ([object()], 123.0))
    monkeypatch.setattr(pipeline, "save_pull", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "clear_flag", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "set_flag", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    return attempts


def test_maybe_refresh_positions_retries_transient_flex_failures_before_success(monkeypatch):
    attempts = _patch_successful_position_refresh(monkeypatch, attempts_before_success=3)
    monkeypatch.setattr(pipeline, "_populate_ir_urls_after_position_refresh", lambda positions: {"updated": 0})

    result = pipeline.maybe_refresh_positions(
        force=True,
        max_attempts=3,
        retry_sleep_seconds=0,
    )

    assert result["refreshed"] is True
    assert result["reason"] == "ok"
    assert result["attempts"] == 3
    assert len(attempts) == 3


def test_maybe_refresh_positions_can_skip_noncritical_ir_population(monkeypatch):
    attempts = _patch_successful_position_refresh(monkeypatch)

    def fail_if_ir_population_runs(positions):
        raise AssertionError("IR URL population should not run during position-only refresh")

    monkeypatch.setattr(pipeline, "_populate_ir_urls_after_position_refresh", fail_if_ir_population_runs)

    result = pipeline.maybe_refresh_positions(
        force=True,
        max_attempts=3,
        retry_sleep_seconds=0,
        populate_ir_urls=False,
    )

    assert result["refreshed"] is True
    assert result["reason"] == "ok"
    assert result["attempts"] == 1
    assert result["ir_urls"] == {"status": "skipped", "reason": "disabled"}
    assert len(attempts) == 1

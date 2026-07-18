from __future__ import annotations

from pathlib import Path

SCRIPT = Path("scripts/sma_weekend_full_refresh.py")


def test_weekend_refresh_covers_dashboard_sources_and_ratings():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "maybe_refresh_positions" in source
    assert "refresh_eod_target_upside" in source
    assert 'EXPECTED_CLOSE_DATE = "2026-07-17"' in source
    assert "bootstrap_ai_draft_sidecars" in source
    assert "recompute_all_with_refresh" in source
    assert '"catalyst_outlooks"' in source


def test_weekend_refresh_has_safe_scheduler_contract():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "SMA_CRON_SELF_TEST" in source
    assert "SECRET_RE" in source
    assert "6 * 60 * 60" in source

"""Scheduler cadence tests."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sma_monitor.orchestrator.schedule import (
    COLLECT_TIME_ET,
    crontab_lines,
    is_in_firing_window,
    next_firing_at,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def test_collect_fires_on_weekends_too():
    sunday = datetime(2026, 5, 31, 18, 5, tzinfo=ET).astimezone(UTC)
    assert is_in_firing_window(COLLECT_TIME_ET, now_utc=sunday)


def test_next_firing_does_not_skip_weekend():
    saturday_before_collect = datetime(2026, 5, 30, 12, 0, tzinfo=ET).astimezone(UTC)
    nxt = next_firing_at(COLLECT_TIME_ET, now_utc=saturday_before_collect).astimezone(ET)
    assert nxt.date().isoformat() == "2026-05-30"
    assert nxt.time().hour == 18


def test_crontab_collect_is_daily():
    lines = "\n".join(crontab_lines())
    assert "0 18 * * *" in lines
    assert "0 9 * * *" in lines

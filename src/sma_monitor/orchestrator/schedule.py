"""Sleep-loop scheduler + crontab generator.

Two deployment options (PLAN §0):
  1. Long-running process — `python -m sma_monitor.orchestrator run`
     Sleep-loops with market-hour awareness. Use under systemd / supervisor.
  2. Cron-driven — `python -m sma_monitor.orchestrator install-cron` emits a
     crontab snippet you can append with `crontab -e`.

Cadences (PLAN §2C):
  News poll: every 10 min during market hours, hourly overnight, skip weekends.
  Positions: refresh on stale (>12h) at start of each cycle.
  Digest:    once per day around 20:15 UTC (~4:15 PM ET).

The market-hour calc is a static UTC window (not DST-aware). When DST shifts,
either tune the constants here or move to a TZ-aware library.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime, timezone

log = logging.getLogger("sma_monitor.orchestrator.schedule")

# Rough US-market hours in UTC (EST winter: 14:30-21:00; EDT summer: 13:30-20:00).
# Default to the EDT window since it's more permissive — over-polling is cheap.
MARKET_OPEN_UTC = dtime(13, 30)
MARKET_CLOSE_UTC = dtime(20, 0)
DIGEST_TIME_UTC = dtime(20, 15)        # ~4:15 PM ET
POLL_INTERVAL_MARKET_SECS = 600         # 10 min
POLL_INTERVAL_OVERNIGHT_SECS = 3600     # 1 hr


def is_market_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now.timetz().replace(tzinfo=None)
    return MARKET_OPEN_UTC <= t < MARKET_CLOSE_UTC


def is_weekend(now: datetime | None = None) -> bool:
    return (now or datetime.now(timezone.utc)).weekday() >= 5


def next_interval_secs(*, reduce_frequency: bool = False, now: datetime | None = None) -> int:
    """Seconds to sleep until the next cycle should kick off."""
    now = now or datetime.now(timezone.utc)
    if is_weekend(now):
        # Skip weekends; long sleep, but cap so we re-evaluate every hour.
        return POLL_INTERVAL_OVERNIGHT_SECS
    base = POLL_INTERVAL_MARKET_SECS if is_market_hours(now) else POLL_INTERVAL_OVERNIGHT_SECS
    return base * 2 if reduce_frequency else base


def is_digest_window(now: datetime | None = None, *, slack_minutes: int = 30) -> bool:
    """Within slack_minutes after DIGEST_TIME_UTC on a weekday."""
    now = now or datetime.now(timezone.utc)
    if is_weekend(now):
        return False
    target = now.replace(hour=DIGEST_TIME_UTC.hour, minute=DIGEST_TIME_UTC.minute,
                         second=0, microsecond=0)
    delta = (now - target).total_seconds()
    return 0 <= delta <= slack_minutes * 60


def crontab_lines() -> list[str]:
    """Emit a crontab snippet for the cron-host deployment."""
    return [
        "# SMA monitor — append to your crontab (`crontab -e`).",
        "# Paths assume an installed package; replace WORKDIR/VENV as needed.",
        "WORKDIR=/opt/sma-monitor",
        "VENV=$WORKDIR/.venv",
        "",
        "# Positions: 9:05 ET and 15:35 ET (DST-naive — tune for your TZ).",
        "5  13  *  *  1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.portfolio pull >> data/logs/cron.log 2>&1",
        "35 19  *  *  1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.portfolio pull >> data/logs/cron.log 2>&1",
        "",
        "# News + score + red team + alerts: every 10 min during market hours.",
        "*/10 13-20 *  *  1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator tick >> data/logs/cron.log 2>&1",
        "",
        "# Hourly overnight cycle (lower cadence).",
        "0  21-23,0-12 *  *  1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator tick >> data/logs/cron.log 2>&1",
        "",
        "# Evening digest, ~4:15 PM ET.",
        "15 20  *  *  1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator tick --with-digest >> data/logs/cron.log 2>&1",
    ]


def run_loop(
    *,
    offline: bool = False,
    one_iteration: bool = False,
    interval_override_secs: int | None = None,
) -> None:
    """Long-running scheduler. Wakes, runs one_cycle, sleeps."""
    from .cost import current_degrade_state
    from .pipeline import run_one_cycle

    digest_done_for_date: str | None = None
    while True:
        now = datetime.now(timezone.utc)
        is_weekend_now = is_weekend(now)

        # PLAN §2C: off-hours skip weekends. We still tick once an hour so
        # any deferred work that landed on Friday afternoon gets cleaned up.
        if is_weekend_now and not one_iteration:
            log.info("weekend_skip", extra={"sleep_secs": POLL_INTERVAL_OVERNIGHT_SECS})
            time.sleep(POLL_INTERVAL_OVERNIGHT_SECS)
            continue

        today_iso = now.date().isoformat()
        should_run_digest = (
            is_digest_window(now) and digest_done_for_date != today_iso
        )
        cycle_state = run_one_cycle(
            offline=offline, include_digest=should_run_digest,
        )
        if should_run_digest:
            digest_done_for_date = today_iso

        if one_iteration:
            return

        degrade = current_degrade_state()
        secs = interval_override_secs or next_interval_secs(
            reduce_frequency=degrade.reduce_poll_frequency, now=now
        )
        log.info("loop_sleep",
                 extra={"sleep_secs": secs,
                        "reduce_frequency": degrade.reduce_poll_frequency,
                        "next_run_at": (
                            datetime.now(timezone.utc).timestamp() + secs
                        )})
        time.sleep(secs)

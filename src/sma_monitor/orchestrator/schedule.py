"""Daily scheduler — thesis email at 9 AM ET, collect at 6 PM ET, dispatch at 9 PM ET.

Three timed firings per weekday, no continuous polling. Eastern time is
DST-aware via zoneinfo (stdlib 3.9+). PLAN §0 runtime host remains either
systemd's long-running loop or cron — same logic underneath.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("sma_monitor.orchestrator.schedule")

# Eastern timezone — zoneinfo handles EST/EDT transitions automatically so
# 6 PM ET stays 6 PM through DST shifts. UTC kept around for log fields.
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Three daily firing times. Morning sends the thesis-drift email; collect runs
# the data-gathering pipeline (and recomputes decisions); dispatch assembles +
# sends the digest three hours later so scoring/red-team have a generous budget.
MORNING_TIME_ET = dtime(9, 0)       # 9:00 AM ET — recompute stale decisions + thesis email
COLLECT_TIME_ET = dtime(18, 0)      # 6:00 PM ET — positions + news + score + red-team + decide
DISPATCH_TIME_ET = dtime(21, 0)     # 9:00 PM ET — digest assembly + delivery

# Slack window around each firing. Handles cron jitter and manual `run`
# starts that miss the exact minute by a few minutes.
FIRING_SLACK_MINUTES = 30


# Return True when the current UTC time falls within the slack window after
# the given ET firing time on a weekday. Used to decide whether a cycle
# should run "now" vs sleep until the next scheduled firing.
def is_in_firing_window(target_et: dtime, *, now_utc: datetime | None = None) -> bool:
    now_utc = now_utc or datetime.now(tz=UTC)
    now_et = now_utc.astimezone(ET)
    if now_et.weekday() >= 5:
        return False
    target_dt = now_et.replace(hour=target_et.hour, minute=target_et.minute,
                                second=0, microsecond=0)
    delta_seconds = (now_et - target_dt).total_seconds()
    return 0 <= delta_seconds <= FIRING_SLACK_MINUTES * 60


# Compute the next absolute UTC datetime at which `target_et` will fire.
# Skips weekends so the result always lands Mon-Fri.
def next_firing_at(target_et: dtime, *, now_utc: datetime | None = None) -> datetime:
    now_utc = now_utc or datetime.now(tz=UTC)
    now_et = now_utc.astimezone(ET)
    candidate = now_et.replace(hour=target_et.hour, minute=target_et.minute,
                                second=0, microsecond=0)
    if candidate <= now_et:
        candidate = candidate + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)


# Emit a crontab snippet for the cron-driven deployment. TZ=America/New_York
# pins the times to ET so cron itself handles EST/EDT transitions — no
# manual offset edits twice a year.
def crontab_lines() -> list[str]:
    """Emit a crontab snippet for the cron-host deployment."""
    return [
        "# SMA monitor — daily firings: 9 AM ET (thesis email), 6 PM ET (collect), 9 PM ET (dispatch).",
        "# TZ pin lets cron handle EST/EDT transitions automatically.",
        "TZ=America/New_York",
        "WORKDIR=/opt/sma-monitor",
        "VENV=$WORKDIR/.venv",
        "",
        "# 9 AM ET — recompute stale thesis-drift decisions + send the morning email.",
        "0 9 * * 1-5   cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator thesis-email >> data/logs/cron.log 2>&1",
        "",
        "# 6 PM ET — refresh positions, poll news, score, red team, recompute decisions.",
        "0 18 * * 1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator collect >> data/logs/cron.log 2>&1",
        "",
        "# 9 PM ET — assemble and dispatch the digest.",
        "0 21 * * 1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator dispatch >> data/logs/cron.log 2>&1",
    ]


# Long-running scheduler loop for the systemd deployment. Sleeps until the
# next of the three firings (morning thesis / collect / dispatch), runs the
# matching cycle, then loops back to compute the next firing.
def run_loop(
    *,
    offline: bool = False,
    one_iteration: bool = False,
) -> None:
    """Long-running scheduler. Wakes at next firing, runs its cycle, sleeps."""
    from .pipeline import run_collect_cycle, run_dispatch_cycle, run_morning_thesis_cycle

    firings = [
        (MORNING_TIME_ET, "morning_thesis", run_morning_thesis_cycle),
        (COLLECT_TIME_ET, "collect", run_collect_cycle),
        (DISPATCH_TIME_ET, "dispatch", run_dispatch_cycle),
    ]

    while True:
        now_utc = datetime.now(tz=UTC)
        # Pick the soonest upcoming firing across all three.
        target, cycle_name, cycle_fn = min(
            ((next_firing_at(t, now_utc=now_utc), name, fn) for t, name, fn in firings),
            key=lambda x: x[0],
        )

        sleep_secs = max(60, int((target - now_utc).total_seconds()))
        log.info("loop_sleep",
                 extra={"next_cycle": cycle_name,
                        "next_fire_at_utc": target.isoformat(),
                        "sleep_secs": sleep_secs})
        time.sleep(sleep_secs)

        cycle_fn(offline=offline)

        if one_iteration:
            return

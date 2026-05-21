"""Daily scheduler — collect at 6 PM ET, dispatch at 9 PM ET.

Two timed firings per weekday, no continuous polling. Eastern time is
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

# Two daily firing times. Collect runs the data-gathering pipeline; dispatch
# assembles + sends the digest three hours later so the scoring and red-team
# stages have a generous budget to finish.
COLLECT_TIME_ET = dtime(18, 0)      # 6:00 PM ET — positions + news + score + red-team
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
        "# SMA monitor — daily firings at 6 PM ET (collect) and 9 PM ET (dispatch).",
        "# TZ pin lets cron handle EST/EDT transitions automatically.",
        "TZ=America/New_York",
        "WORKDIR=/opt/sma-monitor",
        "VENV=$WORKDIR/.venv",
        "",
        "# 6 PM ET — refresh positions, poll news, score, red team.",
        "0 18 * * 1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator collect >> data/logs/cron.log 2>&1",
        "",
        "# 9 PM ET — assemble and dispatch the digest.",
        "0 21 * * 1-5  cd $WORKDIR && $VENV/bin/python -m sma_monitor.orchestrator dispatch >> data/logs/cron.log 2>&1",
    ]


# Long-running scheduler loop for the systemd deployment. Sleeps until the
# next firing (collect or dispatch, whichever comes first), runs the right
# cycle, then loops back to compute the next firing.
def run_loop(
    *,
    offline: bool = False,
    one_iteration: bool = False,
) -> None:
    """Long-running scheduler. Wakes at next firing, runs its cycle, sleeps."""
    from .pipeline import run_collect_cycle, run_dispatch_cycle

    while True:
        now_utc = datetime.now(tz=UTC)
        next_collect = next_firing_at(COLLECT_TIME_ET, now_utc=now_utc)
        next_dispatch = next_firing_at(DISPATCH_TIME_ET, now_utc=now_utc)
        if next_collect <= next_dispatch:
            target = next_collect
            cycle_name = "collect"
        else:
            target = next_dispatch
            cycle_name = "dispatch"

        sleep_secs = max(60, int((target - now_utc).total_seconds()))
        log.info("loop_sleep",
                 extra={"next_cycle": cycle_name,
                        "next_fire_at_utc": target.isoformat(),
                        "sleep_secs": sleep_secs})
        time.sleep(sleep_secs)

        if cycle_name == "collect":
            run_collect_cycle(offline=offline)
        else:
            run_dispatch_cycle(offline=offline)

        if one_iteration:
            return

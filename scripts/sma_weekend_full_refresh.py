#!/usr/bin/env python3
"""One-time pre-open AI-CRO dashboard refresh wrapper.

The scheduled run refreshes the broker snapshot, FMP targets and Friday close,
research evidence, catalysts, and every holding's rating. Full output is kept
in the app log directory while stdout contains a compact scheduler summary.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/data/sma-monitor")
ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "data" / "logs"
EXPECTED_CLOSE_DATE = "2026-07-17"
SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|auth|database_url)\s*[:=]\s*[^\s,'\"]+"
)

FULL_REFRESH_PY = f"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sma_monitor.analyst_targets.service import refresh_eod_target_upside
from sma_monitor.config import settings
from sma_monitor.orchestrator.manual_recompute import recompute_all_with_refresh
from sma_monitor.orchestrator.pipeline import maybe_refresh_positions
from sma_monitor.portfolio.draft_thesis import bootstrap_ai_draft_sidecars


def capture(fn):
    try:
        return fn()
    except Exception as exc:
        return {{"status": "failed", "error": str(exc)[:300]}}


state = {{"started_at": datetime.now(UTC).isoformat(), "mode": "weekend_full_refresh"}}
state["positions"] = maybe_refresh_positions(
    force=True,
    max_attempts=3,
    retry_sleep_seconds=60,
    populate_ir_urls=True,
)
if not state["positions"].get("refreshed"):
    raise RuntimeError(
        "Portfolio refresh failed; refusing to update a potentially stale holding set"
    )

state["analyst_targets"] = capture(
    lambda: refresh_eod_target_upside(
        api_key=settings.fmp_api_key,
        retry_attempts=1,
        retry_seconds=0,
        expected_price_date="{EXPECTED_CLOSE_DATE}",
    )
)
state["new_position_drafts"] = capture(
    lambda: bootstrap_ai_draft_sidecars(
        compute_source="scheduler_weekend_preopen",
    )
)
state["dashboard"] = recompute_all_with_refresh(offline=False, force=True)
state["finished_at"] = datetime.now(UTC).isoformat()
print(json.dumps(state, indent=2, default=str))
"""

COMMAND = "PYTHONPATH=src .venv/bin/python - <<'PY'\n" + FULL_REFRESH_PY + "\nPY"


def _redact(text: str) -> str:
    return SECRET_RE.sub(lambda match: match.group(1) + "=[REDACTED]", text)


def _run(command: str, *, timeout: int = 6 * 60 * 60) -> subprocess.CompletedProcess[str]:
    shell_command = (
        "set -euo pipefail; "
        f"cd {ROOT}; "
        "set -a; . ./.env; set +a; "
        f"{command}"
    )
    return subprocess.run(
        ["/bin/bash", "-lc", shell_command],
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _parse_json(stdout: str) -> dict:
    text = stdout.strip()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            return json.loads(text[index:])
        except json.JSONDecodeError:
            continue
    return {}


def _compact(state: dict) -> dict:
    dashboard = state.get("dashboard") or {}
    refresh = dashboard.get("refresh") or {}
    targets = state.get("analyst_targets") or {}
    return {
        "mode": state.get("mode"),
        "positions": state.get("positions"),
        "targets": {
            "source": targets.get("source"),
            "tickers": targets.get("tickers"),
            "eligible_equities": targets.get("eligible_equities"),
            "skipped_etfs": targets.get("skipped_etfs"),
            "updated": targets.get("updated"),
            "no_target": targets.get("no_target"),
            "no_price": targets.get("no_price"),
            "expected_price_date": targets.get("expected_price_date"),
        },
        "new_position_drafts": state.get("new_position_drafts"),
        "refresh": {
            key: refresh.get(key)
            for key in (
                "news",
                "literature",
                "sec",
                "financials",
                "prices",
                "catalyst_outlooks",
            )
        },
        "scoring": dashboard.get("scoring"),
        "red_team": dashboard.get("red_team"),
        "decisions": dashboard.get("decisions"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
    }


def _self_test() -> int:
    checks = {
        "root_exists": ROOT.is_dir(),
        "env_exists": ENV_FILE.is_file(),
        "python_exists": (ROOT / ".venv" / "bin" / "python").is_file(),
    }
    result = _run(
        "PYTHONPATH=src .venv/bin/python - <<'PY'\n"
        "import json\n"
        "from sma_monitor.analyst_targets.service import refresh_eod_target_upside\n"
        "from sma_monitor.orchestrator.manual_recompute import recompute_all_with_refresh\n"
        "from sma_monitor.orchestrator.pipeline import maybe_refresh_positions\n"
        "print(json.dumps({'imports': all((refresh_eod_target_upside, "
        "recompute_all_with_refresh, maybe_refresh_positions))}))\n"
        "PY",
        timeout=60,
    )
    runtime = _parse_json(result.stdout)
    summary = {"self_test": checks, "runtime": runtime, "exit_code": result.returncode}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all(checks.values()) and runtime.get("imports") and result.returncode == 0 else 1


def main() -> int:
    if os.environ.get("SMA_CRON_SELF_TEST") == "1":
        return _self_test()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    log_path = LOG_DIR / (
        "hermes_weekend_full_refresh_"
        f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.log"
    )
    result = _run(COMMAND)
    log_path.write_text(
        _redact(f"# stdout\n{result.stdout}\n\n# stderr\n{result.stderr}\n"),
        encoding="utf-8",
    )
    state = _parse_json(result.stdout)
    summary = {
        "job": "sma_weekend_full_refresh",
        "exit_code": result.returncode,
        "log_path": str(log_path),
        "summary": _compact(state),
    }
    if result.returncode:
        summary["stderr_tail"] = _redact(result.stderr[-1200:])
        summary["stdout_tail"] = _redact(result.stdout[-1200:])
    print(json.dumps(summary, indent=2, sort_keys=True))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

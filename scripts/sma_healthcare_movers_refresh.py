#!/usr/bin/env python3
"""Hermes bridge for the nightly US healthcare mover refresh."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_CANDIDATES = (
    Path("/opt/data/sma-monitor"),
    Path(__file__).resolve().parents[1],
)
REPO = next(path for path in REPO_CANDIDATES if (path / "src" / "sma_monitor").is_dir())
VENV_PYTHON = REPO / ".venv" / "bin" / "python"


def _worker() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from sma_monitor.config import settings
    from sma_monitor.healthcare_movers.service import refresh_healthcare_movers

    summary = refresh_healthcare_movers(
        api_key=settings.fmp_api_key,
        bootstrap=os.environ.get("SMA_HEALTHCARE_MOVERS_BOOTSTRAP") == "1",
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["status"] == "current" else 1


def _self_test() -> int:
    sys.path.insert(0, str(REPO / "src"))
    try:
        from sma_monitor.healthcare_movers.service import refresh_healthcare_movers
    except ImportError:
        refresh_healthcare_movers = None
    checks = {
        "repo": REPO.is_dir(),
        "venv_python": VENV_PYTHON.is_file(),
        "refresh_import": refresh_healthcare_movers is not None,
    }
    print(json.dumps({"self_test": checks}, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


def main() -> int:
    if "--worker" in sys.argv:
        try:
            return _worker()
        except Exception as exc:  # noqa: BLE001 - scheduler output stays compact.
            print(json.dumps({"error_type": type(exc).__name__, "error": str(exc)[:300]}))
            return 1
    if os.environ.get("SMA_CRON_SELF_TEST") == "1":
        return _self_test()
    if not VENV_PYTHON.is_file():
        print(json.dumps({"error_type": "RepositoryVirtualenvMissing"}))
        return 127
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    result = subprocess.run(
        [str(VENV_PYTHON), str(Path(__file__).resolve()), "--worker"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=2 * 60 * 60,
    )
    print(result.stdout.strip() or json.dumps({"error_type": "RefreshFailed"}))
    if result.returncode and result.stderr:
        print(json.dumps({"stderr_tail": result.stderr[-1000:]}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

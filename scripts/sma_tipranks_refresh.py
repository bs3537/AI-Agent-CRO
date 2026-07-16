#!/usr/bin/env python3
"""Hermes pre-run script for the weekly TipRanks target refresh."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Resolve the app repo whether this file runs from Git or Hermes' scripts directory.
REPO_CANDIDATES = (
    Path("/opt/data/sma-monitor"),
    Path(__file__).resolve().parents[1],
)
REPO = next(path for path in REPO_CANDIDATES if (path / "src" / "sma_monitor").is_dir())
VENV_PYTHON = REPO / ".venv" / "bin" / "python"


# Optional bounded ticker set for acceptance checks; production leaves it unset.
def _ticker_override() -> list[str] | None:
    raw = os.environ.get("SMA_ANALYST_TARGET_TICKERS", "")
    tickers = [value.strip().upper() for value in raw.split(",") if value.strip()]
    return tickers or None


# Execute the application logic only inside the repository virtualenv.
def _worker() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from sma_monitor.analyst_targets.service import refresh_tipranks_targets

    summary = refresh_tipranks_targets(tickers=_ticker_override())
    print(json.dumps(summary, indent=2, default=str))
    return 0


# Hermes invokes this file with its own Python, so bridge to the app environment.
def main() -> int:
    if "--worker" in sys.argv:
        try:
            return _worker()
        except Exception as exc:  # noqa: BLE001 - emit only a scheduler-safe error.
            print(json.dumps({"error_type": type(exc).__name__}))
            return 1
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
        timeout=3600,
    )
    if result.returncode != 0:
        print(result.stdout.strip() or json.dumps({"error_type": "RefreshFailed"}))
        return result.returncode
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Workstream 5 CLI — run the API server.

  python -m sma_monitor.api [--host H] [--port N] [--reload]

Defaults to 127.0.0.1:8000. Use --host 0.0.0.0 to expose on a VM. In
production a process supervisor (systemd, W8) runs this; --reload is dev-only.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn


# Entry point: parse host/port/reload and hand off to uvicorn. Passes the
# import string (not the app object) so --reload can re-import on changes.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sma_monitor.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload (dev only)")
    args = parser.parse_args(argv)

    uvicorn.run(
        "sma_monitor.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

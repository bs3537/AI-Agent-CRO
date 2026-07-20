"""Healthcare-mover refresh CLI."""
from __future__ import annotations

import argparse
import json

from ..config import settings
from ..logging_setup import setup_logging
from .service import refresh_healthcare_movers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sma_monitor.healthcare_movers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    refresh = subparsers.add_parser("refresh", help="Refresh universe, prices, and rankings")
    refresh.add_argument(
        "--bootstrap",
        action="store_true",
        help="Re-fetch history for every active symbol",
    )
    args = parser.parse_args(argv)
    setup_logging(settings.log_level)
    result = refresh_healthcare_movers(
        api_key=settings.fmp_api_key,
        bootstrap=args.bootstrap,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "current" else 1


if __name__ == "__main__":
    raise SystemExit(main())

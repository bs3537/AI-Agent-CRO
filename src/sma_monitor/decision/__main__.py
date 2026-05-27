"""Workstream 3 CLI.

  python -m sma_monitor.decision recompute [--ticker T] [--offline] [--limit N] [--force]
  python -m sma_monitor.decision show [--ticker T]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from .engine import run_decisions
from .store import init_decision_schema, latest_decision, latest_decisions

# Map a stored verdict to a glanceable indicator for the terminal view.
_DOT = {"sell": "🔴 SELL ", "watch": "🟡 WATCH", "hold": "🟢 HOLD "}


# CLI: (re)compute thesis-drift decisions. Skips unchanged holdings unless
# --force; --offline forces the heuristic verdict (no model call).
def cmd_recompute(args, log):
    res = run_decisions(
        offline=args.offline,
        limit=args.limit,
        only_ticker=args.ticker,
        force=args.force,
    )
    log.info("decision_recompute_done", extra=res)
    print(
        f"decided={res['decided']} skipped={res['skipped']} "
        f"errors={res['errors']} holdings={res['holdings']}"
    )
    return 0


# CLI: print the latest decision per ticker (or one ticker's detail).
def cmd_show(args, log):
    if args.ticker:
        row = latest_decision(args.ticker)
        if row is None:
            print(f"(no decision for {args.ticker.upper()})")
            return 0
        _print_detail(row)
        return 0
    rows = latest_decisions()
    if not rows:
        print("(no decisions — run `recompute` first)")
        return 0
    print(f"{'VERDICT':<8}  {'TICKER':<8}  {'CONF':>4}  DRIVERS")
    for r in rows:
        try:
            drivers = json.loads(r["drivers"] or "[]")
        except json.JSONDecodeError:
            drivers = []
        drv = "; ".join(drivers[:2]) or "—"
        print(f"{_DOT[r['verdict']]:<8}  {r['ticker']:<8}  {r['confidence']:>4.2f}  {drv[:70]}")
    return 0


# Pretty-print one decision row in full (used by `show --ticker`).
def _print_detail(r) -> None:
    try:
        drivers = json.loads(r["drivers"] or "[]")
    except json.JSONDecodeError:
        drivers = []
    print(f"{_DOT[r['verdict']]}  {r['ticker']}   (confidence {r['confidence']:.2f}, "
          f"model {r['model_used']}, decided {r['decided_at']})")
    print("\nNOTE:")
    print(r["note"])
    if drivers:
        print("\nDRIVERS:")
        for d in drivers:
            print(f"  - {d}")


# Workstream 3 CLI entry point. Wires the two subcommands to their handlers.
def main(argv=None):
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.decision")
    init_decision_schema()

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.decision")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rc = sub.add_parser("recompute", help="(Re)compute thesis-drift decisions")
    p_rc.add_argument("--ticker", help="Limit to one ticker")
    p_rc.add_argument("--offline", action="store_true",
                      help="Use the heuristic verdict (no model call)")
    p_rc.add_argument("--limit", type=int, help="Cap the number of holdings processed")
    p_rc.add_argument("--force", action="store_true",
                      help="Recompute even if thesis + evidence are unchanged")

    p_show = sub.add_parser("show", help="Print latest decision per ticker")
    p_show.add_argument("--ticker", help="Show one ticker's decision in full")

    args = parser.parse_args(argv)
    handlers = {"recompute": cmd_recompute, "show": cmd_show}
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

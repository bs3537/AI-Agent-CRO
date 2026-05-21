"""Phase 1 CLI.

  python -m sma_monitor.portfolio pull
  python -m sma_monitor.portfolio pull --from-file path/to/flex.xml
  python -m sma_monitor.portfolio show
  python -m sma_monitor.portfolio show-joined
  python -m sma_monitor.portfolio validate-sidecar
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from .flex import (
    FlexError,
    fetch_statement,
    load_xml_from_file,
    parse_positions,
)
from .joined import latest_joined
from .sidecar import load_all_sidecars
from .store import init_portfolio_schema, latest_positions, save_pull


# CLI: fetch a Flex statement (live or from file), parse, persist. Returns
# 0 on success, 1 on Flex failure, 2 on missing credentials.
def cmd_pull(args: argparse.Namespace, log: logging.Logger) -> int:
    if args.from_file:
        log.info("flex_load_from_file", extra={"path": args.from_file})
        raw = load_xml_from_file(Path(args.from_file))
        source = f"file:{args.from_file}"
    else:
        missing = settings.missing_for(1)
        if missing:
            log.error("flex_missing_secrets", extra={"missing": missing})
            return 2
        assert settings.ibkr_flex_token and settings.ibkr_flex_query_id
        log.info("flex_send_request")
        try:
            raw = fetch_statement(
                token=settings.ibkr_flex_token,
                query_id=settings.ibkr_flex_query_id,
            )
        except FlexError as e:
            log.error("flex_fetch_failed", extra={"err": str(e)})
            return 1
        source = "ibkr_flex"

    try:
        positions, nav = parse_positions(raw.xml, pulled_at=raw.pulled_at)
    except FlexError as e:
        log.error("flex_parse_failed", extra={"err": str(e)})
        return 1

    pull_id = save_pull(
        positions,
        nav=nav,
        pulled_at=raw.pulled_at,
        source=source,
        raw_xml=None if args.no_raw else raw.xml,
    )
    log.info(
        "flex_pull_ok",
        extra={"pull_id": pull_id, "nav": nav, "positions": len(positions)},
    )
    return 0


# CLI: print the latest pull's positions table to stdout.
def cmd_show(args: argparse.Namespace, log: logging.Logger) -> int:
    positions, pulled_at = latest_positions()
    if pulled_at is None:
        log.warning("no_positions_yet")
        return 0
    print(f"Latest pull: {pulled_at.isoformat()}  ({len(positions)} positions)")
    print(f"{'TICKER':<8} {'QTY':>12} {'MKT_VALUE':>14} {'%NAV':>8} {'COST_BASIS':>14}")
    for p in positions:
        cb = f"{p.cost_basis:>14,.2f}" if p.cost_basis is not None else f"{'-':>14}"
        print(
            f"{p.ticker:<8} {p.qty:>12,.2f} {p.market_value:>14,.2f} "
            f"{p.pct_nav * 100:>7,.2f}% {cb}"
        )
    return 0


# CLI: print the joined Holding view (Position ⨝ Sidecar) and list any
# positions missing a sidecar so the user can fix the gap.
def cmd_show_joined(args: argparse.Namespace, log: logging.Logger) -> int:
    holdings, missing, pulled_at = latest_joined()
    if pulled_at is None:
        log.warning("no_positions_yet")
        return 0
    print(f"Joined holdings  ({pulled_at.isoformat()})")
    print(
        f"{'TICKER':<8} {'%NAV':>7} {'TIER':>4} {'STAGE':<17} "
        f"{'NEAREST':>9} {'CATS':>4}"
    )
    for h in holdings:
        if h.nearest_catalyst_days is None:
            nearest = "-"
        else:
            nearest = f"{h.nearest_catalyst_days}d"
        if h.has_overdue_catalyst:
            nearest = f"{nearest}!"
        print(
            f"{h.ticker:<8} {h.pct_nav * 100:>6,.2f}% {h.conviction_tier:>4} "
            f"{h.stage:<17} {nearest:>9} {len(h.catalysts):>4}"
        )
    if missing:
        print()
        print(f"WARNING: missing sidecar for {len(missing)} positions: {', '.join(missing)}")
    return 0


# CLI: validate every sidecar YAML and warn about unresolved catalysts whose
# dates are in the past. Implements PLAN §1's sidecar maintenance protocol.
def cmd_validate(args: argparse.Namespace, log: logging.Logger) -> int:
    try:
        scs = load_all_sidecars()
    except Exception as e:
        log.error("sidecar_invalid", extra={"err": str(e)})
        return 1
    log.info("sidecar_validated", extra={"count": len(scs), "tickers": sorted(scs.keys())})
    today = datetime.now(timezone.utc).date()
    overdue_any = False
    for sc in scs.values():
        overdue = [c for c in sc.catalysts if not c.resolved and c.date < today]
        if overdue:
            overdue_any = True
            log.warning(
                "sidecar_overdue_catalysts",
                extra={
                    "ticker": sc.ticker,
                    "count": len(overdue),
                    "dates": [c.date.isoformat() for c in overdue],
                },
            )
    if overdue_any:
        log.warning("sidecar_maintenance_due")
    return 0


# Phase 1 CLI entry point. Wires the four subcommands to their handlers.
def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.portfolio")
    init_portfolio_schema()

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.portfolio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pull = sub.add_parser("pull", help="Fetch IBKR Flex statement and store positions")
    p_pull.add_argument("--from-file", help="Replay a saved Flex XML instead of calling IBKR")
    p_pull.add_argument("--no-raw", action="store_true", help="Don't persist raw XML")

    sub.add_parser("show", help="Print latest positions")
    sub.add_parser("show-joined", help="Print joined holdings (positions ⨝ sidecar)")
    sub.add_parser("validate-sidecar", help="Validate all sidecar YAMLs, flag overdue catalysts")

    args = parser.parse_args(argv)
    handlers = {
        "pull": cmd_pull,
        "show": cmd_show,
        "show-joined": cmd_show_joined,
        "validate-sidecar": cmd_validate,
    }
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

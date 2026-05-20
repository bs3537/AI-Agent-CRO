"""Phase 4 CLI.

  python -m sma_monitor.red_team run [--offline] [--limit N]
  python -m sma_monitor.red_team show [--ticker T] [--min-severity N] [--limit N]
  python -m sma_monitor.red_team show-catalog [--bucket N]
  python -m sma_monitor.red_team library-coverage
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from .catalog import by_bucket, load_catalog
from .pipeline import run_red_team
from .store import init_red_team_schema, library_coverage, recent_passes


def cmd_run(args, log):
    res = run_red_team(
        api_key=settings.anthropic_api_key,
        offline=args.offline,
        limit=args.limit,
    )
    log.info("red_team_run_done", extra=res)
    return 0


def cmd_show(args, log):
    rows = recent_passes(
        ticker=args.ticker,
        min_severity=args.min_severity,
        limit=args.limit,
    )
    if not rows:
        print("(no red-team passes)")
        return 0
    print(f"{'SEV':>3}  {'COMP':>6}  {'TICKER':<8}  {'BAND':<9}  {'PATTERNS':<35}  TITLE")
    for r in rows:
        try:
            ws = json.loads(r["matched_warning_signs"] or "[]")
        except json.JSONDecodeError:
            ws = []
        pat = ", ".join(m["id"] for m in ws[:2]) or "—"
        if len(ws) > 2:
            pat += f", +{len(ws) - 2}"
        title = (r["title"] or "")[:70]
        print(
            f"{r['severity_of_concern']:>3}  {r['composite']:>6.2f}  "
            f"{r['ticker']:<8}  {r['threshold_band']:<9}  {pat:<35}  {title}"
        )
        print(f"      thesis: {r['bearish_thesis']}")
        print(f"      invalidator: {r['invalidator']}")
    return 0


def cmd_show_catalog(args, log):
    cat = load_catalog()
    print(f"Warning-signs catalog v{cat.catalog_version}  ({len(cat.warning_signs)} entries)")
    if args.bucket:
        grouped = by_bucket(cat)
        entries = grouped.get(args.bucket, [])
        print(f"\n--- bucket #{args.bucket} ({len(entries)} entries) ---")
        for ws in entries:
            print(f"\n[{ws.id}] {ws.name}  (buckets {ws.buckets})")
            print(f"  PATTERN:     {ws.definition.strip()}")
            print(f"  INVALIDATOR: {ws.invalidator.strip()}")
        return 0
    grouped = by_bucket(cat)
    for bid in sorted(grouped):
        print(f"\n--- bucket #{bid} ({len(grouped[bid])} entries) ---")
        for ws in grouped[bid]:
            print(f"  [{ws.id:<40}]  {ws.name}")
    return 0


def cmd_library_coverage(args, log):
    cat = load_catalog()
    counts = library_coverage(cat.catalog_version)
    print(f"Library coverage at catalog v{cat.catalog_version}:")
    print(f"  ({len(cat.warning_signs)} entries; {sum(counts.values())} citations across red-team passes)\n")
    print(f"  {'COUNT':>5}  {'BUCKET(S)':<10}  WARNING SIGN")
    for ws in cat.warning_signs:
        n = counts.get(ws.id, 0)
        flag = "" if n > 0 else "  UNUSED"
        bks = ",".join(str(b) for b in ws.buckets)
        print(f"  {n:>5}  {bks:<10}  {ws.id}{flag}")
    return 0


def main(argv=None):
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.red_team")
    init_red_team_schema()

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.red_team")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Red-team every above-T₂ score lacking a pass")
    p_run.add_argument("--offline", action="store_true",
                       help="Use heuristic red team (no API call)")
    p_run.add_argument("--limit", type=int)

    p_show = sub.add_parser("show", help="Print recent red-team passes")
    p_show.add_argument("--ticker")
    p_show.add_argument("--min-severity", type=int)
    p_show.add_argument("--limit", type=int, default=20)

    p_cat = sub.add_parser("show-catalog", help="Print warning-signs catalog")
    p_cat.add_argument("--bucket", type=int)

    sub.add_parser("library-coverage", help="Citation counts per warning sign")

    args = parser.parse_args(argv)
    handlers = {
        "run": cmd_run, "show": cmd_show,
        "show-catalog": cmd_show_catalog,
        "library-coverage": cmd_library_coverage,
    }
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

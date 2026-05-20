"""Phase 5 CLI.

  python -m sma_monitor.outputs alerts [--min-composite N] [--dry-run]
  python -m sma_monitor.outputs digest [--date YYYY-MM-DD] [--with-narrative]
  python -m sma_monitor.outputs show-alerts [--ticker T] [--limit N] [--all]
  python -m sma_monitor.outputs show-digest [--date YYYY-MM-DD]
  python -m sma_monitor.outputs mark <event_id> {useful|noise} [--kind] [--note]
  python -m sma_monitor.outputs mark-missed --description "..." [--ticker T]
                                            [--bucket N] [--url URL] [--note "..."]
  python -m sma_monitor.outputs feedback-list
"""
from __future__ import annotations

import argparse
import logging
import sys

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from . import feedback as fb
from .alerts import run_alerts
from .channels import StdoutChannel, build_channels
from .digest import assemble_digest
from .store import (
    init_outputs_schema, list_feedback, list_missed, recent_alerts,
)
from ..db import connection


def cmd_alerts(args, log):
    channels = build_channels(prefer_stdout=False)
    res = run_alerts(
        min_composite=args.min_composite,
        dry_run=args.dry_run,
        channels=channels,
    )
    log.info("alerts_done", extra=res)
    return 0


def cmd_digest(args, log):
    channels = build_channels(prefer_stdout=False)
    if args.print:
        channels = list(channels) + [StdoutChannel()]
    res = assemble_digest(
        date_iso=args.date,
        with_narrative=args.with_narrative,
        api_key=settings.anthropic_api_key,
        channels=channels,
    )
    log.info("digest_done", extra=res)
    return 0


def cmd_show_alerts(args, log):
    rows = recent_alerts(
        ticker=args.ticker,
        limit=args.limit,
        include_suppressed=args.all,
    )
    if not rows:
        print("(no alerts)")
        return 0
    for r in rows:
        flag = " [SUPPRESSED]" if r["suppressed"] else ""
        print(f"=== {r['alerted_at']}  {r['ticker']}  composite {r['composite']:.2f}{flag} ===")
        if r["suppressed"]:
            print(f"  reason: {r['suppression_reason']}")
        print(r["rendered_text"])
        print()
    return 0


def cmd_show_digest(args, log):
    init_outputs_schema()
    with connection() as conn:
        if args.date:
            row = conn.execute(
                "SELECT * FROM digests WHERE date = ?", (args.date,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM digests ORDER BY date DESC LIMIT 1"
            ).fetchone()
    if row is None:
        print("(no digest)")
        return 0
    print(row["rendered_md"])
    return 0


def cmd_mark(args, log):
    fid = fb.mark(args.event_id, args.mark, kind=args.kind, note=args.note)
    log.info("feedback_saved", extra={"feedback_id": fid, "target": args.event_id,
                                     "mark": args.mark})
    return 0


def cmd_mark_missed(args, log):
    mid = fb.mark_missed(
        ticker=args.ticker,
        bucket_id_guess=args.bucket,
        description=args.description,
        article_url=args.url,
        note=args.note,
    )
    log.info("missed_saved", extra={"missed_id": mid, "ticker": args.ticker,
                                   "bucket": args.bucket})
    return 0


def cmd_feedback_list(args, log):
    print("--- feedback (useful/noise marks) ---")
    rows = list_feedback(limit=args.limit)
    if not rows:
        print("(none)")
    else:
        for r in rows:
            note = f"  note: {r['note']}" if r["note"] else ""
            print(f"  {r['marked_at']}  {r['mark']:<6}  {r['target_kind']:<8}  "
                  f"{r['target_id'][:24]}…{note}")
    print()
    print("--- missed events ---")
    missed = list_missed(limit=args.limit)
    if not missed:
        print("(none)")
        return 0
    for r in missed:
        note = f"  note: {r['note']}" if r["note"] else ""
        ticker = r["ticker"] or "—"
        print(f"  {r['recorded_at']}  {ticker:<8}  bucket {r['bucket_id_guess'] or '—'}  "
              f"{r['description'][:70]}{note}")
    return 0


def main(argv=None):
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.outputs")
    init_outputs_schema()

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.outputs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_alerts = sub.add_parser("alerts", help="Process scores >= T into alerts")
    p_alerts.add_argument("--min-composite", type=float,
                          help="Override T threshold (default = T from multipliers.py)")
    p_alerts.add_argument("--dry-run", action="store_true",
                          help="Render and record decisions, but don't dispatch")

    p_digest = sub.add_parser("digest", help="Assemble + dispatch evening digest")
    p_digest.add_argument("--date", help="YYYY-MM-DD (default: today, UTC)")
    p_digest.add_argument("--with-narrative", action="store_true",
                          help="Generate Opus narrative section (or template fallback)")
    p_digest.add_argument("--print", action="store_true",
                          help="Also print the digest to stdout")

    p_sa = sub.add_parser("show-alerts", help="Print recent alerts")
    p_sa.add_argument("--ticker")
    p_sa.add_argument("--limit", type=int, default=10)
    p_sa.add_argument("--all", action="store_true", help="Include suppressed")

    p_sd = sub.add_parser("show-digest", help="Print one digest")
    p_sd.add_argument("--date")

    p_m = sub.add_parser("mark", help="Mark an alert or score event_id useful/noise")
    p_m.add_argument("event_id")
    p_m.add_argument("mark", choices=["useful", "noise"])
    p_m.add_argument("--kind", choices=["alert", "score", "digest_event"], default="alert")
    p_m.add_argument("--note")

    p_mm = sub.add_parser("mark-missed", help="Record an event the agent didn't surface")
    p_mm.add_argument("--description", required=True)
    p_mm.add_argument("--ticker")
    p_mm.add_argument("--bucket", type=int)
    p_mm.add_argument("--url")
    p_mm.add_argument("--note")

    p_fl = sub.add_parser("feedback-list", help="Show feedback + missed events")
    p_fl.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    handlers = {
        "alerts": cmd_alerts, "digest": cmd_digest,
        "show-alerts": cmd_show_alerts, "show-digest": cmd_show_digest,
        "mark": cmd_mark, "mark-missed": cmd_mark_missed,
        "feedback-list": cmd_feedback_list,
    }
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

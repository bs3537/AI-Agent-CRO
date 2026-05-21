"""Phase 7 CLI.

  python -m sma_monitor.tuning report [--days N]               write + print full report
  python -m sma_monitor.tuning precision [--days N]            just the precision table
  python -m sma_monitor.tuning library-candidates [--days N]   missed events → YAML drafts
  python -m sma_monitor.tuning conviction-review               tier distribution + stale flags
  python -m sma_monitor.tuning bucket-review [--days N]        PLAN §7 questions
"""
from __future__ import annotations

import argparse
import logging
import sys

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from .bucket_review import (
    bucket_10_overlap,
    bucket_11_cluster_shape,
    cyber_breach_density,
    render_bucket_review_markdown,
    silent_buckets,
)
from .conviction_review import (
    render_tier_markdown,
    stale_sidecars,
    tier_distribution,
)
from .library_growth import (
    library_growth_candidates,
    render_candidates_markdown,
)
from .precision import (
    alert_precision_by_bucket,
    render_precision_markdown,
)
from .report import assemble_report, write_report
from .weights import (
    render_suggestions_markdown,
    suggest_weight_adjustments,
    threshold_recommendations,
)


# CLI: write the full tuning report to data/tuning/YYYY-MM-DD.md. Pass
# --print to also dump the markdown to stdout.
def cmd_report(args, log):
    p = write_report(window_days=args.days)
    log.info("report_written", extra={"path": str(p), "days": args.days})
    if args.print:
        print(p.read_text())
    else:
        print(f"Report written to {p}")
    return 0


# CLI: print only the precision table + weight/threshold suggestions.
def cmd_precision(args, log):
    rows = alert_precision_by_bucket(days=args.days)
    print(render_precision_markdown(rows, days=args.days))
    print()
    suggestions = suggest_weight_adjustments(rows)
    thresh = threshold_recommendations(rows)
    print(render_suggestions_markdown(suggestions, thresh))
    return 0


# CLI: print library-growth candidates (missed events → draft YAML blocks).
def cmd_library_candidates(args, log):
    candidates = library_growth_candidates(days=args.days)
    print(render_candidates_markdown(candidates))
    return 0


# CLI: print the conviction-review section — tier distribution + stale sidecars.
def cmd_conviction_review(args, log):
    print(render_tier_markdown(tier_distribution(), stale_sidecars()))
    return 0


# CLI: print the bucket-architecture review section — silent buckets +
# the three PLAN §7 questions.
def cmd_bucket_review(args, log):
    print(render_bucket_review_markdown(
        silent_buckets(days=args.days),
        bucket_10_overlap(days=max(args.days, 28)),
        bucket_11_cluster_shape(days=max(args.days, 56)),
        cyber_breach_density(days=max(args.days, 60)),
        days=args.days,
    ))
    return 0


# Phase 7 CLI entry point. Wires all subcommands to their handlers.
def main(argv=None):
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.tuning")

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.tuning")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rep = sub.add_parser("report", help="Write the full markdown tuning report")
    p_rep.add_argument("--days", type=int, default=14,
                       help="Window for precision / silent-bucket queries (default 14)")
    p_rep.add_argument("--print", action="store_true",
                       help="Also print the report to stdout")

    p_pr = sub.add_parser("precision", help="Per-bucket alert precision + suggestions")
    p_pr.add_argument("--days", type=int, default=14)

    p_lc = sub.add_parser("library-candidates", help="Missed events → catalog.yaml drafts")
    p_lc.add_argument("--days", type=int, default=90)

    sub.add_parser("conviction-review", help="Tier distribution + stale sidecars")

    p_br = sub.add_parser("bucket-review", help="PLAN §7 architecture questions")
    p_br.add_argument("--days", type=int, default=14)

    args = parser.parse_args(argv)
    handlers = {
        "report": cmd_report, "precision": cmd_precision,
        "library-candidates": cmd_library_candidates,
        "conviction-review": cmd_conviction_review,
        "bucket-review": cmd_bucket_review,
    }
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

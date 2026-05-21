"""Phase 3 CLI.

  python -m sma_monitor.scorer score [--offline] [--limit N]
  python -m sma_monitor.scorer show [--ticker T] [--band above_t|t2_to_t|below_t2]
                                    [--min N] [--limit N]
  python -m sma_monitor.scorer show-rubric
  python -m sma_monitor.scorer show-multipliers
  python -m sma_monitor.scorer calibrate [--offline]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from . import calibration as calib
from .multipliers import (
    BUCKET_WEIGHTS, CONVICTION_MULT, MULTIPLIERS_VERSION, T, T2, position_weight,
)
from .pipeline import score_unscored
from .rubric import RUBRIC_TEXT
from .store import init_scores_schema, recent_scores


# CLI: score every (article, ticker) pair lacking a row at the current
# MULTIPLIERS_VERSION. Pass --offline to force the heuristic scorer.
def cmd_score(args, log):
    res = score_unscored(
        api_key=settings.anthropic_api_key,
        offline=args.offline,
        limit=args.limit,
    )
    log.info("scoring_summary", extra=res)
    return 0


# CLI: print a recent-scores table. Optional filters on ticker, band,
# and minimum composite.
def cmd_show(args, log):
    rows = recent_scores(
        ticker=args.ticker,
        band=args.band,
        min_composite=args.min,
        limit=args.limit,
    )
    if not rows:
        print("(no scores)")
        return 0
    print(f"{'COMP':>6}  {'BAND':<9}  {'TIER':>4}  {'TICKER':<8}  {'BUCKET':>6}  "
          f"{'F':>4}  {'N':>4}  {'T':>4}  {'CONF':>5}  TITLE")
    for r in rows:
        title = (r["title"] or "")[:70]
        print(
            f"{r['composite']:>6.2f}  {r['threshold_band']:<9}  "
            f"{r['source_tier'] or 5:>4}  {r['ticker']:<8}  "
            f"{r['primary_bucket_id']:>6}  "
            f"{r['financial_impact']:>4.1f}  {r['narrative_shift']:>4.1f}  "
            f"{r['time_criticality']:>4.1f}  {r['confidence']:>5.2f}  {title}"
        )
    return 0


# CLI: dump the rubric text to stdout. Useful for verifying what the
# scorer model is being told to do.
def cmd_show_rubric(args, log):
    print(RUBRIC_TEXT)
    return 0


# CLI: dump every multiplier + threshold value + the position-weight curve.
# Use after changing multipliers.py to sanity-check the numbers.
def cmd_show_multipliers(args, log):
    print(f"MULTIPLIERS_VERSION: {MULTIPLIERS_VERSION}")
    print(f"Thresholds:  T (alert) = {T}    T₂ (digest red-team) = {T2}")
    print()
    print("BUCKET_WEIGHTS:")
    for bid, w in sorted(BUCKET_WEIGHTS.items()):
        print(f"  bucket {bid:>2}: {w:.2f}")
    print()
    print("CONVICTION_MULT (sidecar tier → multiplier):")
    for tier in sorted(CONVICTION_MULT):
        print(f"  tier {tier}: {CONVICTION_MULT[tier]:.2f}×")
    print()
    print("position_weight(%NAV)  [log scale]:")
    for p in (0.005, 0.01, 0.03, 0.05, 0.10, 0.15, 0.20, 0.24):
        print(f"  {p * 100:>6.2f}%  →  {position_weight(p):.3f}")
    return 0


# CLI: run the calibration harness against data/scores/calibration_set.yaml.
# Exits non-zero on any band mismatch so it's safe to use in CI.
def cmd_calibrate(args, log):
    rows = calib.run(api_key=settings.anthropic_api_key, offline=args.offline)
    if not rows:
        print("(empty calibration set — edit data/scores/calibration_set.yaml)")
        return 0
    misses = sum(1 for r in rows if r.get("match") is False)
    print(f"Calibration ({len(rows)} entries, {misses} mismatches, "
          f"version={MULTIPLIERS_VERSION}):\n")
    for r in rows:
        if "err" in r:
            print(f"  ERROR  {r['name']}: {r['err']}")
            continue
        mark = "ok " if r["match"] else "MISS"
        print(
            f"  {mark}  {r['name']:<45}  "
            f"axes={r['axes']}  comp={r['composite']:>6.2f}  "
            f"band={r['band']:<9}  expected={r['expected_band']}"
        )
        if not r["match"]:
            print(f"        rationale: {r['rationale']}")
    return 0 if misses == 0 else 1


# Phase 3 CLI entry point. Wires the five subcommands to their handlers.
def main(argv=None):
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.scorer")
    init_scores_schema()

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.scorer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_score = sub.add_parser("score", help="Score every unscored (article, ticker) pair")
    p_score.add_argument("--offline", action="store_true",
                         help="Use heuristic scorer (no API call)")
    p_score.add_argument("--limit", type=int)

    p_show = sub.add_parser("show", help="Print recent scores")
    p_show.add_argument("--ticker")
    p_show.add_argument("--band", choices=["above_t", "t2_to_t", "below_t2"])
    p_show.add_argument("--min", type=float, help="Minimum composite")
    p_show.add_argument("--limit", type=int, default=30)

    sub.add_parser("show-rubric", help="Print the scoring rubric text")
    sub.add_parser("show-multipliers", help="Print weights, thresholds, position curve")

    p_cal = sub.add_parser("calibrate", help="Score the calibration set and compare to expected_band")
    p_cal.add_argument("--offline", action="store_true")

    args = parser.parse_args(argv)
    handlers = {
        "score": cmd_score, "show": cmd_show,
        "show-rubric": cmd_show_rubric, "show-multipliers": cmd_show_multipliers,
        "calibrate": cmd_calibrate,
    }
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

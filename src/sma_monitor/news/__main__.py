"""Phase 2 CLI.

  python -m sma_monitor.news poll [--ticker T] [--bucket N] [--from-file F]
                                  [--num-results N] [--lookback-hours N]
  python -m sma_monitor.news poll-literature [--ticker T] [--from-file F]   (Scite → #10)
  python -m sma_monitor.news fmp [--ticker T] [--from-file F]               (FMP financials)
  python -m sma_monitor.news show [--ticker T] [--bucket N] [--limit N]
  python -m sma_monitor.news show-buckets
  python -m sma_monitor.news show-queries [--ticker T]
  python -m sma_monitor.news coverage [--days N]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from ..portfolio.joined import latest_joined
from .buckets import load_buckets
from .fmp_client import refresh_for_holdings, refresh_prices_for_holdings
from .pipeline import poll as run_poll
from .pipeline import poll_literature as run_poll_literature
from .query import per_holding_query, sector_query
from .store import bucket_activity, init_news_schema, recent_articles


# CLI: run one news poll cycle. Returns 2 on missing creds, 0 otherwise.
def cmd_poll(args, log):
    try:
        res = run_poll(
            api_key=settings.exa_api_key,
            from_file=Path(args.from_file) if args.from_file else None,
            filter_ticker=args.ticker,
            filter_bucket=args.bucket,
            num_results=args.num_results,
            lookback_hours=args.lookback_hours,
        )
    except RuntimeError as e:
        log.error("news_poll_missing_creds", extra={"err": str(e)})
        return 2
    log.info("news_poll_done", extra=res)
    return 0


# CLI: run one Scite literature poll (bucket #10). 2 on missing creds.
def cmd_poll_literature(args, log):
    try:
        res = run_poll_literature(
            api_key=settings.scite_api_key,
            from_file=Path(args.from_file) if args.from_file else None,
            filter_ticker=args.ticker,
            num_results=args.num_results,
        )
    except RuntimeError as e:
        log.error("literature_poll_missing_creds", extra={"err": str(e)})
        return 2
    log.info("literature_poll_done", extra=res)
    return 0


# CLI: refresh FMP financial snapshots + daily price series for held tickers.
# 2 on missing creds (neither key nor fixture for the metrics half).
def cmd_fmp(args, log):
    tickers = [args.ticker.upper()] if args.ticker else None
    try:
        res = refresh_for_holdings(
            api_key=settings.fmp_api_key,
            from_file=Path(args.from_file) if args.from_file else None,
            tickers=tickers,
        )
    except RuntimeError as e:
        log.error("fmp_refresh_missing_creds", extra={"err": str(e)})
        return 2
    # Price history (sparkline). Skipped gracefully if no key/fixture.
    try:
        prices = refresh_prices_for_holdings(
            api_key=settings.fmp_api_key,
            from_file=Path(args.prices_file) if args.prices_file else None,
            tickers=tickers,
        )
    except RuntimeError as e:
        log.warning("fmp_prices_skipped", extra={"err": str(e)})
        prices = {"status": "skipped"}
    log.info("fmp_refresh_done", extra={"metrics": res, "prices": prices})
    print({"metrics": res, "prices": prices})
    return 0


# CLI: print recent articles in a tabular view (tier, buckets, tickers, title).
def cmd_show(args, log):
    rows = recent_articles(ticker=args.ticker, bucket_id=args.bucket, limit=args.limit)
    if not rows:
        print("(no articles)")
        return 0
    print(f"{'TIER':>4}  {'BUCKETS':<18}  {'PUBLISHED':<28}  {'TICKERS':<14}  TITLE")
    for r in rows:
        pub = r["published_at"] or r["fetched_at"] or "-"
        bks = r["buckets"] or "-"
        tks = r["tickers"] or "-"
        title = (r["title"] or "")[:90]
        print(f"{(r['source_tier'] or 5):>4}  {bks:<18}  {pub:<28}  {tks:<14}  {title}")
    return 0


# CLI: print the bucket taxonomy in compact form (id, tier, scope, name).
def cmd_show_buckets(args, log):
    buckets = load_buckets()
    print(f"{'ID':>3}  {'TIER':<4}  {'SCOPE':<13}  {'TERMS':>5}  NAME")
    for b in sorted(buckets.values(), key=lambda b: b.id):
        print(
            f"{b.id:>3}   {b.build_tier:<4} {b.scope:<13}  "
            f"{len(b.search_terms):>5}  {b.name}"
        )
    return 0


# CLI: dry-run that prints the queries the next poll would issue. Useful
# for inspecting recall before burning Exa quota.
def cmd_show_queries(args, log):
    holdings, _, _ = latest_joined()
    if not holdings:
        log.warning("no_holdings")
        return 0
    if args.ticker:
        holdings = [h for h in holdings if h.ticker == args.ticker.upper()]
    buckets = load_buckets()
    for h in holdings:
        print(f"\n=== {h.ticker} ===")
        for b in sorted(buckets.values(), key=lambda b: b.id):
            if b.scope == "per_holding":
                print(f"  [{b.id:>2} {b.short_name:<13}] {per_holding_query(h, b)}")
    print("\n=== sector ===")
    for b in sorted(buckets.values(), key=lambda b: b.id):
        if b.scope == "sector":
            print(f"  [{b.id:>2} {b.short_name:<13}] {sector_query(b)}")
    return 0


# CLI: bucket-activity audit — articles per bucket over the last N days,
# flagging silent buckets (probable ingestion gaps).
def cmd_coverage(args, log):
    counts = bucket_activity(days=args.days)
    buckets = load_buckets()
    print(f"Bucket activity (last {args.days} days):")
    for bid, b in sorted(buckets.items()):
        n = counts.get(bid, 0)
        flag = "  SILENT" if n == 0 else ""
        print(f"  [{bid:>2} {b.short_name:<14}] {n:>4} articles{flag}")
    return 0


# Phase 2 CLI entry point. Wires subcommands to handlers.
def main(argv=None):
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.news")
    init_news_schema()

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.news")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_poll = sub.add_parser("poll", help="Run a news poll across holdings × buckets")
    p_poll.add_argument("--ticker", help="Restrict to one holding")
    p_poll.add_argument("--bucket", type=int, help="Restrict to one bucket id")
    p_poll.add_argument("--from-file", help="Load Exa response from JSON fixture")
    p_poll.add_argument("--num-results", type=int, default=5)
    p_poll.add_argument("--lookback-hours", type=int, default=24)

    p_lit = sub.add_parser("poll-literature", help="Scite literature poll → bucket #10")
    p_lit.add_argument("--ticker", help="Restrict to one holding")
    p_lit.add_argument("--from-file", help="Load Scite response from JSON fixture")
    p_lit.add_argument("--num-results", type=int, default=5)

    p_fmp = sub.add_parser("fmp", help="Refresh FMP financials + daily price series")
    p_fmp.add_argument("--ticker", help="Restrict to one ticker")
    p_fmp.add_argument("--from-file", help="Load {ticker: metrics} JSON fixture")
    p_fmp.add_argument("--prices-file", help="Load {ticker: [closes]} price fixture (sparkline)")

    p_show = sub.add_parser("show", help="Print recent articles")
    p_show.add_argument("--ticker")
    p_show.add_argument("--bucket", type=int)
    p_show.add_argument("--limit", type=int, default=20)

    sub.add_parser("show-buckets", help="Print the bucket taxonomy")

    p_sq = sub.add_parser("show-queries", help="Print what queries the next poll would run")
    p_sq.add_argument("--ticker")

    p_cov = sub.add_parser("coverage", help="Bucket activity audit (Phase 5 digest footer)")
    p_cov.add_argument("--days", type=int, default=14)

    args = parser.parse_args(argv)
    handlers = {
        "poll": cmd_poll,
        "poll-literature": cmd_poll_literature,
        "fmp": cmd_fmp,
        "show": cmd_show,
        "show-buckets": cmd_show_buckets,
        "show-queries": cmd_show_queries,
        "coverage": cmd_coverage,
    }
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

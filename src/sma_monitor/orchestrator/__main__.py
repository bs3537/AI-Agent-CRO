"""Phase 6 CLI.

  python -m sma_monitor.orchestrator collect [--offline]     daily 6 PM ET step
  python -m sma_monitor.orchestrator dispatch [--offline]    daily 9 PM ET step
  python -m sma_monitor.orchestrator thesis-recompute [--offline]  daily 6 AM ET step
  python -m sma_monitor.orchestrator thesis-email      daily 9:15 AM ET delivery step
  python -m sma_monitor.orchestrator tick [--offline] [--with-digest]   ad-hoc all-in-one
  python -m sma_monitor.orchestrator run [--offline]         long-running scheduler
  python -m sma_monitor.orchestrator status
  python -m sma_monitor.orchestrator process-runner-requests [--limit N]
  python -m sma_monitor.orchestrator runner-run
  python -m sma_monitor.orchestrator runner-requests
  python -m sma_monitor.orchestrator install-cron
  python -m sma_monitor.orchestrator retry-dead-letters [--offline]
  python -m sma_monitor.orchestrator simulate-spend --usd N
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime

from ..config import settings
from ..logging_setup import setup_logging
from ..paths import ensure_dirs
from . import dead_letter as dl
from .cost import current_degrade_state, today_spent_usd
from .flags import clear_flag, get_active_flags
from .pipeline import (
    run_collect_cycle,
    run_dispatch_cycle,
    run_morning_thesis_delivery_cycle,
    run_morning_thesis_recompute_cycle,
    run_one_cycle,
)
from .schedule import crontab_lines, run_loop
from .store import (
    cost_by_kind_since,
    init_orchestrator_schema,
    save_cost_row,
)


# CLI: run the daily 6 PM ET collection step. Refreshes positions, polls
# news, scores, runs the red team. No alerts, no digest.
def cmd_collect(args, log):
    state = run_collect_cycle(offline=args.offline)
    log.info("collect_done", extra={"keys": list(state.keys())})
    print(json.dumps(state, indent=2, default=str))
    return 0


# CLI: run the daily 9 PM ET dispatch step. Assembles the digest with the
# Opus narrative and sends via every configured channel (email + file).
def cmd_dispatch(args, log):
    state = run_dispatch_cycle(offline=args.offline)
    log.info("dispatch_done", extra={"keys": list(state.keys())})
    print(json.dumps(state, indent=2, default=str))
    return 0


# CLI: run the daily 6 AM ET smart thesis-drift recompute step. It refreshes
# all daily signals, then recomputes only triggered holdings. It intentionally
# does not send email; the delivery command waits if this is still active.
def cmd_thesis_recompute(args, log):
    state = run_morning_thesis_recompute_cycle(
        offline=args.offline,
        force=not args.no_force,
    )
    log.info("thesis_recompute_done", extra={"keys": list(state.keys())})
    print(json.dumps(state, indent=2, default=str))
    return 0


# CLI: run the daily 9:15 AM ET thesis-drift email delivery step. It waits for
# the recompute flag to clear before sending.
def cmd_thesis_email(args, log):
    state = run_morning_thesis_delivery_cycle(
        wait_for_recompute=not args.no_wait,
        wait_timeout_minutes=args.wait_minutes,
    )
    log.info("thesis_email_done", extra={"keys": list(state.keys())})
    print(json.dumps(state, indent=2, default=str))
    return 0


# CLI: ad-hoc all-in-one cycle for manual testing. Use collect/dispatch
# for the scheduled daily firings; this is the "do everything now" path.
def cmd_tick(args, log):
    state = run_one_cycle(
        offline=args.offline,
        include_digest=args.with_digest,
        news_fixture=args.news_fixture,
    )
    log.info("tick_done", extra={"keys": list(state.keys())})
    print(json.dumps(state, indent=2, default=str))
    return 0


# CLI: start the long-running scheduler loop — sleeps until each daily
# firing (6 PM ET collect, 9 PM ET dispatch), runs the cycle, repeats.
# Intended to run under systemd or another process supervisor.
def cmd_run(args, log):
    log.info("run_loop_starting", extra={"offline": args.offline})
    run_loop(offline=args.offline)
    return 0


# CLI: print today's operational state — spend vs budget, cascade flags,
# spend by kind, active system flags, and dead-letter rows.
def cmd_status(args, log):
    flags = get_active_flags()
    degrade = current_degrade_state()
    today_kind = cost_by_kind_since(
        datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    pending = dl.pending()
    all_dl = dl.all_recent(limit=20)

    print("=== Orchestrator status ===")
    print(f"Today's spend:   ${degrade.spent_usd:.4f} / ${degrade.budget_usd:.2f} "
          f"({degrade.fraction_spent*100:.1f}%)")
    print("Degrade cascade:")
    print(f"  step 1 — skip red team T₂–T band:  {degrade.skip_red_team_t2_t_band}")
    print(f"  step 2 — skip Opus narrative:      {degrade.skip_opus_narrative}")
    print(f"  step 3 — drop buckets #10/#11:     {degrade.drop_buckets_10_11}")
    print(f"  step 4 — drop bucket #12:          {degrade.drop_bucket_12}")
    print()
    print("Spend by kind today:")
    if not today_kind:
        print("  (no Claude calls recorded today)")
    else:
        for r in today_kind:
            print(f"  {r['kind']:<20}  ${r['cost_usd']:>8.4f}  "
                  f"({r['n_calls']} calls, {r['total_tokens']} tokens)")
    print()
    print(f"Active system flags ({len(flags)}):")
    if not flags:
        print("  (none)")
    else:
        for f in flags:
            meta = f.get("metadata") or ""
            print(f"  {f['flag_name']:<26} set_at={f['set_at']}  {meta}")
    print()
    print(f"Dead letters: {len(pending)} pending, {len(all_dl) - len(pending)} other")
    for r in all_dl[:10]:
        print(f"  [{r['status']:<10}] {r['kind']:<10} {r['ticker'] or '—':<8} "
              f"attempts={r['attempt_count']}  err={(r['error'] or '')[:60]}")
    return 0


def cmd_process_runner_requests(args, log):
    from .runner import process_runner_requests

    summary = process_runner_requests(limit=args.limit, offline=args.offline)
    log.info("runner_requests_processed", extra=summary)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_runner_run(args, log):
    from .runner import run_runner_loop

    log.info(
        "runner_loop_starting",
        extra={"offline": args.offline, "poll_seconds": args.poll_seconds, "limit": args.limit},
    )
    run_runner_loop(offline=args.offline, poll_seconds=args.poll_seconds, batch_limit=args.limit)
    return 0


def cmd_runner_requests(args, log):
    from .store import recent_runner_requests

    print(json.dumps(recent_runner_requests(limit=args.limit), indent=2, default=str))
    return 0


# CLI: emit a crontab snippet for the cron-driven deployment. Pipe through
# `crontab -` or paste into `crontab -e`.
def cmd_install_cron(args, log):
    for line in crontab_lines():
        print(line)
    return 0


# CLI: re-run scorer + red-team pipelines so 'pending' dead-letter rows
# get one more attempt. A second failure transitions them to 'abandoned'.
def cmd_retry_dead_letters(args, log):
    """Re-run scorer + red-team pipelines; rows still failing will bump
    attempt_count and (on second failure) be marked 'abandoned'."""
    from ..red_team.pipeline import run_red_team
    from ..scorer.pipeline import score_unscored

    pending = dl.pending()
    log.info("retry_dead_letters_start", extra={"n_pending": len(pending)})
    score_res = score_unscored(api_key=settings.anthropic_api_key, offline=args.offline)
    rt_res = run_red_team(api_key=settings.anthropic_api_key, offline=args.offline)
    print(json.dumps({"pending_before": len(pending),
                      "score_res": score_res, "red_team_res": rt_res,
                      "pending_after": len(dl.pending())},
                     indent=2, default=str))
    return 0


# CLI: insert a synthetic cost ledger row to exercise the degrade cascade
# without actually burning Claude tokens. Tagged 'synthetic_simulation' so
# real cost analysis can filter it out.
def cmd_simulate_spend(args, log):
    """Append a synthetic cost row — useful for testing the degrade cascade.

    Uses the heuristic model label so it doesn't affect live cost accounting
    if you forget to remove it. Use --usd to control the amount.
    """
    # Manually insert a row at the requested cost — synthetic for cascade testing
    init_orchestrator_schema()
    save_cost_row(
        kind="synthetic_simulation", model="claude-sonnet-4-6",
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=float(args.usd),
        related_event_id=None,
        incurred_at=datetime.now(UTC),
    )
    print(json.dumps({"simulated_usd": args.usd,
                      "today_total_after": today_spent_usd(),
                      "degrade_state": cmd_status.__doc__ or ""},
                     indent=2))
    log.info("synthetic_spend_added", extra={"usd": args.usd})
    return 0


# CLI: manually clear a system flag by name. Useful when an external
# condition has resolved but the orchestrator hasn't observed it yet.
def cmd_clear_flag(args, log):
    clear_flag(args.name)
    log.info("flag_cleared", extra={"flag_name": args.name})
    return 0


# Phase 6 CLI entry point. Wires every subcommand to its handler.
def main(argv=None):
    ensure_dirs()
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.orchestrator")
    init_orchestrator_schema()

    parser = argparse.ArgumentParser(prog="python -m sma_monitor.orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_collect = sub.add_parser("collect",
                               help="Daily 6 PM ET — positions, news, score, red-team")
    p_collect.add_argument("--offline", action="store_true",
                           help="Use heuristic scorer + red team (no API calls)")

    p_dispatch = sub.add_parser("dispatch",
                                help="Daily 9 PM ET — assemble + send digest")
    p_dispatch.add_argument("--offline", action="store_true",
                            help="Skip the Opus narrative call")

    p_thesis_recompute = sub.add_parser(
        "thesis-recompute",
        help="Daily 6 AM ET — smart thesis recompute before email",
    )
    p_thesis_recompute.add_argument("--offline", action="store_true",
                                    help="Use the heuristic decision verdict (no model call)")
    p_thesis_recompute.add_argument("--no-force", action="store_true",
                                    help="Only recompute holdings with changed inputs")

    p_thesis = sub.add_parser(
        "thesis-email",
        help="Daily 9:15 AM ET — send morning email after the LLM recompute window",
    )
    p_thesis.add_argument("--offline", action="store_true", help=argparse.SUPPRESS)
    p_thesis.add_argument("--no-wait", action="store_true",
                          help="Do not wait for a running thesis recompute flag")
    p_thesis.add_argument("--wait-minutes", type=int, default=360,
                          help="Max minutes to wait for thesis recompute to finish")

    p_tick = sub.add_parser("tick", help="Ad-hoc all-in-one cycle (manual testing)")
    p_tick.add_argument("--offline", action="store_true",
                        help="Use heuristic scorer + red team (no API calls)")
    p_tick.add_argument("--with-digest", action="store_true",
                        help="Also assemble + dispatch the evening digest")
    p_tick.add_argument("--news-fixture", help="Path to JSON fixture (offline news)")

    p_run = sub.add_parser("run", help="Long-running scheduler loop")
    p_run.add_argument("--offline", action="store_true")

    sub.add_parser("status", help="Today's spend, degrade cascade, flags, dead letters")
    p_runner_once = sub.add_parser(
        "process-runner-requests",
        aliases=["hermes-once"],
        help="Hermes/VPS: process queued dashboard runner requests once",
    )
    p_runner_once.add_argument("--limit", type=int, default=1)
    p_runner_once.add_argument("--offline", action="store_true")

    p_runner_run = sub.add_parser(
        "runner-run",
        aliases=["hermes-run"],
        help="Hermes/VPS: poll and process queued dashboard runner requests",
    )
    p_runner_run.add_argument("--limit", type=int, default=1, help="Requests per poll")
    p_runner_run.add_argument("--poll-seconds", type=int, default=10)
    p_runner_run.add_argument("--offline", action="store_true")

    p_runner_list = sub.add_parser(
        "runner-requests",
        help="List recent dashboard-enqueued runner requests",
    )
    p_runner_list.add_argument("--limit", type=int, default=20)
    sub.add_parser("install-cron", help="Emit a crontab snippet")

    p_retry = sub.add_parser("retry-dead-letters", help="Re-attempt pending dead-lettered scores")
    p_retry.add_argument("--offline", action="store_true")

    p_sim = sub.add_parser("simulate-spend",
                           help="Insert a synthetic cost row to test the degrade cascade")
    p_sim.add_argument("--usd", type=float, required=True)

    p_cf = sub.add_parser("clear-flag", help="Manually clear a system flag")
    p_cf.add_argument("name")

    args = parser.parse_args(argv)
    handlers = {
        "collect": cmd_collect,
        "dispatch": cmd_dispatch,
        "thesis-recompute": cmd_thesis_recompute,
        "thesis-email": cmd_thesis_email,
        "tick": cmd_tick,
        "run": cmd_run,
        "status": cmd_status,
        "process-runner-requests": cmd_process_runner_requests,
        "hermes-once": cmd_process_runner_requests,
        "runner-run": cmd_runner_run,
        "hermes-run": cmd_runner_run,
        "runner-requests": cmd_runner_requests,
        "install-cron": cmd_install_cron,
        "retry-dead-letters": cmd_retry_dead_letters,
        "simulate-spend": cmd_simulate_spend,
        "clear-flag": cmd_clear_flag,
    }
    return handlers[args.cmd](args, log)


if __name__ == "__main__":
    sys.exit(main())

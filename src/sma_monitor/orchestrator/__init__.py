"""Phase 6 — Orchestration & reliability.

Modules:
  store        — cost_ledger / system_flags / dead_letters tables
  cost         — per-call token tracking, DegradeState (PLAN §6 cascade)
  flags        — set/clear/list operational flags (surfaced in digest)
  dead_letter  — record + retry policy for scorer / red-team failures
  pipeline     — run_one_cycle: pull → poll → score → red team → alerts
                 with degrade-state aware throttling
  schedule     — sleep-loop driver with market-hour awareness; crontab
                 generator for the cron-host deployment option
  __main__     — CLI: tick / run / status / install-cron / retry-dead-letters
"""

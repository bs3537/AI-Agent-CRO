# Codex Runner Fresh Evidence Only Plan

Date: 2026-06-07

## Problem

The deployed Hermes morning cron path was not using the smart thesis-drift gate. It forced a full-book Codex review every morning:

- refreshed all daily signals,
- scored every unscored article/ticker backlog row,
- red-teamed every eligible backlog score,
- forced a decision/rating pass for every current holding.

That is why the Codex runner consumed high usage and queued app requests. Daily drift monitoring should not re-ingest or re-analyze broad historical context. It should pull fresh evidence, compare it against the saved thesis and prior analysis, and alert only when fresh evidence changes the thesis integrity/rating/grade.

## Target Behavior

The scheduled morning review should:

1. Refresh current broker positions first.
2. Pull only fresh company/Yahoo news and SEC filings within the morning lookback window.
3. Build an explicit allow-list of article event IDs fetched by that morning run.
4. Trigger Codex work only for holdings with fresh news or fresh SEC evidence.
5. Score only those fresh article IDs for the triggered ticker.
6. Red-team only fresh scored evidence for the triggered ticker.
7. Build the decision/rating prompt with the saved thesis plus only that fresh scored/red-team evidence.
8. Keep FMP financials, current P&L, and EMA20 as context, but do not let P&L/EMA alone trigger Codex.
9. Leave manual recompute and original-thesis save analysis able to use broader context when the operator explicitly requests it.

## Implemented Changes

- Added article-event-id filters to scorer, red-team, and decision/rating paths.
- Added `article_ids_by_ticker_since()` to the news store so the morning job can pass a fresh-evidence allow-list downstream.
- Added a filed-date cutoff to the SEC EDGAR adapter and `poll_sec(..., lookback_hours=...)`.
- Updated `run_smart_morning_recompute()` so scheduled recompute:
  - refreshes daily signals once,
  - collects fresh article IDs fetched since the run started,
  - triggers Codex only on fresh news/SEC evidence,
  - removes the previous broad `_refresh_evidence()` call for daily triggered tickers,
  - passes fresh article IDs through scoring, red-team, and decision.
- Updated the active Hermes cron wrapper to call the fresh-evidence smart path instead of the prior full-book path.

## Deployment Note

The live Hermes cron wrapper is an operational file outside this git repo:

- `/opt/data/home/scripts/sma_morning_recompute.py`
- `/opt/data/scripts/sma_morning_recompute.py` is a shim that executes the home-script copy.

The repo changes make the app code fresh-evidence-only. The VPS wrapper change is also required so the deployed 6:00 AM Hermes cron job does not keep invoking the old full-book path.

## Expected Impact

Morning Codex usage should scale with actual new evidence, not total holdings or old backlog. A quiet day should do position/news/SEC/price refresh work and send the normal email without forcing 40+ Codex decisions and hundreds of scorer calls.

## Guardrails

- Original thesis save/recompute remains separate from the morning scheduler.
- Manual recompute remains the explicit operator path for broad refresh.
- The morning wrapper still aborts before Codex if the broker position refresh fails.
- The incomplete/running flags still protect the dashboard from silently treating failed recompute as successful.

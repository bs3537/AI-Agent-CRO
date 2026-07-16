"""Hermes/VPS runner for dashboard-enqueued work.

The Replit dashboard writes runner_requests rows into Turso. A trusted Hermes
process with Codex CLI auth drains the queue and runs the existing workflows.
"""
from __future__ import annotations

import json
import logging
import os
import time

from .store import claim_next_runner_request, finish_runner_request, requeue_stale_runner_requests

log = logging.getLogger("sma_monitor.orchestrator.runner")

DEFAULT_POLL_SECONDS = 10


class RunnerRequestError(RuntimeError):
    """Runner failure that can still persist a safe structured summary."""

    def __init__(self, message: str, *, summary: dict | None = None) -> None:
        super().__init__(message)
        self.summary = summary or {}


def process_runner_requests(
    *,
    limit: int = 1,
    offline: bool = False,
    commands: list[str] | tuple[str, ...] | None = None,
) -> dict:
    processed = succeeded = failed = 0
    requeued_stale = requeue_stale_runner_requests(
        max_age_minutes=_stale_request_minutes(commands),
        commands=commands,
    )
    errors: list[dict] = []
    while processed < limit:
        row = claim_next_runner_request(commands=commands)
        if row is None:
            break
        processed += 1
        try:
            summary = _execute(row, offline=offline)
            _raise_for_failed_recompute_summary(row, summary)
            finish_runner_request(row["request_id"], summary=summary)
            succeeded += 1
            log.info(
                "runner_request_succeeded",
                extra={"request_id": row["request_id"], "command": row["command"]},
            )
        except RunnerRequestError as e:
            err = str(e)[:1000]
            finish_runner_request(row["request_id"], summary=e.summary, error=err)
            failed += 1
            errors.append({"request_id": row["request_id"], "error": err})
            log.error(
                "runner_request_failed",
                extra={"request_id": row["request_id"], "command": row["command"], "err": err},
            )
        except Exception as e:  # noqa: BLE001 - queue failures must be captured.
            err = str(e)[:1000]
            finish_runner_request(row["request_id"], error=err)
            failed += 1
            errors.append({"request_id": row["request_id"], "error": err})
            log.exception(
                "runner_request_failed",
                extra={"request_id": row["request_id"], "command": row["command"]},
            )
    return {
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "requeued_stale": requeued_stale,
        "errors": errors,
    }


def _stale_request_minutes(commands: list[str] | tuple[str, ...] | None) -> int:
    raw = os.environ.get("SMA_RUNNER_STALE_MINUTES", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            log.warning("invalid_SMA_RUNNER_STALE_MINUTES", extra={"value": raw})
    command_set = {c.strip() for c in (commands or []) if c and c.strip()}
    if command_set == {"chat_complete"}:
        return 15
    return 75


def run_runner_loop(
    *,
    offline: bool = False,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    batch_limit: int = 1,
    one_iteration: bool = False,
    commands: list[str] | tuple[str, ...] | None = None,
) -> None:
    while True:
        summary = process_runner_requests(limit=batch_limit, offline=offline, commands=commands)
        if one_iteration:
            return
        if summary["processed"] == 0:
            time.sleep(max(1, poll_seconds))


def _execute(row: dict, *, offline: bool) -> dict:
    command = row["command"]
    payload = _payload(row)
    if command == "manual_recompute_one":
        from .manual_recompute import recompute_one_with_refresh

        ticker = (row.get("ticker") or payload.get("ticker") or "").strip().upper()
        if not ticker:
            raise RuntimeError("manual_recompute_one requires ticker")
        return recompute_one_with_refresh(
            ticker,
            offline=bool(payload.get("offline", offline)),
            compute_source=str(payload.get("compute_source") or "hermes_manual_single"),
        )
    if command == "chat_complete":
        from ..chat.service import complete_chat_response

        return complete_chat_response(
            message=str(payload.get("message") or ""),
            history=payload.get("history") or [],
            ticker=payload.get("ticker") or row.get("ticker"),
            include_portfolio=bool(payload.get("include_portfolio", True)),
            attachment_context=str(
                payload.get("attachment_context") or "(no uploaded files in this turn)"
            ),
            attachments=payload.get("attachments") or [],
        )
    if command == "manual_recompute_all":
        from .manual_recompute import recompute_all_with_refresh

        return recompute_all_with_refresh(
            offline=bool(payload.get("offline", offline)),
            force=bool(payload.get("force", True)),
        )
    if command in {"preliminary_thesis_one", "preliminary_thesis_backfill"}:
        from .preliminary_thesis import run_preliminary_thesis_workflow

        ticker = (row.get("ticker") or payload.get("ticker") or "").strip().upper()
        tickers = payload.get("tickers") or ([ticker] if ticker else None)
        if command == "preliminary_thesis_one" and not tickers:
            raise RuntimeError("preliminary_thesis_one requires ticker")
        return run_preliminary_thesis_workflow(
            tickers=tickers,
            limit=payload.get("limit"),
            upgrade_existing_ai=bool(payload.get("upgrade_existing_ai", True)),
            refresh_inputs=bool(payload.get("refresh_inputs", command == "preliminary_thesis_one")),
            compute_source=str(
                payload.get("compute_source") or f"hermes_{command}"
            ),
        )
    if command == "collect":
        from .pipeline import run_collect_cycle

        return run_collect_cycle(offline=bool(payload.get("offline", offline)))
    if command == "thesis_recompute":
        from .pipeline import run_morning_thesis_recompute_cycle

        return run_morning_thesis_recompute_cycle(
            offline=bool(payload.get("offline", offline)),
            force=bool(payload.get("force", True)),
        )
    if command == "thesis_email":
        from .pipeline import run_morning_thesis_delivery_cycle

        return run_morning_thesis_delivery_cycle(
            wait_for_recompute=bool(payload.get("wait_for_recompute", True)),
            wait_timeout_minutes=int(payload.get("wait_timeout_minutes", 360)),
        )
    if command == "dispatch":
        from .pipeline import run_dispatch_cycle

        return run_dispatch_cycle(offline=bool(payload.get("offline", offline)))
    raise RuntimeError(f"unknown runner command: {command}")


def _raise_for_failed_recompute_summary(row: dict, summary: dict) -> None:
    command = row.get("command")
    if command in {"preliminary_thesis_one", "preliminary_thesis_backfill"}:
        drafts = summary.get("drafts") if isinstance(summary, dict) else None
        failures = drafts.get("failed") if isinstance(drafts, dict) else None
        if failures:
            raise RunnerRequestError(
                f"preliminary thesis failed for {len(failures)} ticker(s)",
                summary=summary,
            )
        return
    if command not in {"manual_recompute_one", "manual_recompute_all", "thesis_recompute"}:
        return
    decisions = summary.get("decisions") if isinstance(summary, dict) else None
    if not isinstance(decisions, dict):
        return
    decision_errors = int(decisions.get("errors") or 0)
    if decision_errors <= 0:
        return
    decided = int(decisions.get("decided") or 0)
    skipped = int(decisions.get("skipped") or 0)
    ticker = (row.get("ticker") or "portfolio").strip().upper()
    raise RunnerRequestError(
        f"decision stage failed for {ticker}: "
        f"{decision_errors} error(s), {decided} decided, {skipped} skipped",
        summary=summary,
    )


def _payload(row: dict) -> dict:
    try:
        data = json.loads(row.get("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}

"""LLM throughput controls (Workstream 9).

Two concerns the daily collect cycle needs once the book holds ~40 positions
(scoring + red-team + ~40 decisions + 1 digest = a few hundred LLM calls):

  - Tiering — each stage can run on its own model + reasoning effort. High-volume
    triage stages (scorer, red team) run lighter; low-volume synthesis stages
    (decision, digest) run deeper. Resolved from env so nothing is pinned in code.
  - Bounded concurrency — `map_concurrent` runs the per-item LLM calls in a small
    thread pool (each `codex exec` is its own subprocess, so threads give real
    parallelism). Capped by SMA_LLM_CONCURRENCY.

All knobs are env-driven (SMA_ prefix, matching the SMA_CODEX_* convention in
codex_client) and read at call time so tests can monkeypatch them.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# The stages that resolve their own model/effort. Names match the `stage=`
# argument each pipeline passes to get_provider().
STAGES = ("scorer", "red_team", "decision", "digest")

# Default reasoning effort per stage when no env override is set. Triage stages
# run "medium" (faster/cheaper, high call volume); synthesis stages run "high"
# (few calls, depth matters). Raise to your account's max (e.g. "high"/"xhigh")
# via SMA_CODEX_EFFORT_<STAGE> once you've confirmed the CLI accepts it.
DEFAULT_EFFORT: dict[str, str] = {
    "scorer": "medium",
    "red_team": "medium",
    "decision": "high",
    "digest": "high",
}

# Fallback concurrency when SMA_LLM_CONCURRENCY is unset/invalid, and the hard
# ceiling we clamp to (OpenAI publishes no TPM/concurrency cap; ~3-5 is the
# empirically safe range for one Codex Pro account + backoff).
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 16


# Resolve the model slug for a stage: stage-specific override → global
# SMA_CODEX_MODEL → None (the logged-in account's default model).
def stage_model(stage: str) -> str | None:
    return (
        os.environ.get(f"SMA_CODEX_MODEL_{stage.upper()}")
        or os.environ.get("SMA_CODEX_MODEL")
        or None
    )


# Resolve the reasoning effort for a stage: stage-specific override → global
# SMA_CODEX_EFFORT → the built-in per-stage default. None disables the flag.
def stage_effort(stage: str) -> str | None:
    return (
        os.environ.get(f"SMA_CODEX_EFFORT_{stage.upper()}")
        or os.environ.get("SMA_CODEX_EFFORT")
        or DEFAULT_EFFORT.get(stage)
    )


# Number of concurrent `codex exec` processes the per-item loops may run.
# Parsed from SMA_LLM_CONCURRENCY, clamped to [1, MAX_CONCURRENCY].
def llm_concurrency() -> int:
    raw = os.environ.get("SMA_LLM_CONCURRENCY")
    try:
        n = int(raw) if raw else DEFAULT_CONCURRENCY
    except (TypeError, ValueError):
        n = DEFAULT_CONCURRENCY
    return max(1, min(MAX_CONCURRENCY, n))


# Run `fn` over `items` with up to `workers` threads, returning a list of
# (result, error) tuples in INPUT ORDER. An item whose call raises yields
# (None, exception) rather than aborting the batch — callers inspect the error
# per item (dead-letter, count, etc.). workers<=1 or <=1 item runs inline so
# the offline/heuristic path stays perfectly sequential and deterministic.
def map_concurrent(
    fn: Callable[[Any], Any],
    items: Sequence[Any],
    *,
    workers: int,
) -> list[tuple[Any, BaseException | None]]:
    if workers <= 1 or len(items) <= 1:
        out: list[tuple[Any, BaseException | None]] = []
        for it in items:
            try:
                out.append((fn(it), None))
            except BaseException as e:  # noqa: BLE001 — capture, don't abort batch
                out.append((None, e))
        return out

    results: list[tuple[Any, BaseException | None]] = [(None, None)] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = (fut.result(), None)
            except BaseException as e:  # noqa: BLE001 — capture per-item failure
                results[i] = (None, e)
    return results

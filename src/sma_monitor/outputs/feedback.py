"""Feedback marks (PLAN.MD §5).

mark()          — annotate a past alert / score event_id as useful or noise
mark_missed()   — record an event the agent should have surfaced but didn't.
                  Critical input for Phase 7 warning-signs library growth.

Marks persist by event_id (or composite of fields for missed events) — they
survive re-scoring, catalog updates, etc.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from .store import (
    init_outputs_schema,
    save_feedback,
    save_missed,
)

Mark = Literal["useful", "noise"]
Kind = Literal["alert", "score", "digest_event"]


def mark(
    target_id: str,
    mark: Mark,
    *,
    kind: Kind = "alert",
    note: str | None = None,
) -> str:
    init_outputs_schema()
    return save_feedback(
        target_id=target_id, target_kind=kind, mark=mark, note=note,
        marked_at=datetime.now(timezone.utc),
    )


def mark_missed(
    *,
    ticker: str | None = None,
    bucket_id_guess: int | None = None,
    description: str,
    article_url: str | None = None,
    note: str | None = None,
) -> str:
    init_outputs_schema()
    return save_missed(
        ticker=ticker.upper() if ticker else None,
        bucket_id_guess=bucket_id_guess,
        description=description,
        article_url=article_url,
        note=note,
        recorded_at=datetime.now(timezone.utc),
    )

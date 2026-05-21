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

# Mark values allowed for the `mark` field on feedback rows.
Mark = Literal["useful", "noise"]
# Artifact kinds a feedback row can target.
Kind = Literal["alert", "score", "digest_event"]


# Record one feedback mark against a past event_id. Persists via store.save_feedback
# with a stable feedback_event_id so re-marking the same target replaces cleanly.
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


# Record a missed event — something the agent should have surfaced but
# didn't. Feeds Phase 7 library-growth: each row is a candidate for a new
# warning-sign entry once the user adds keywords + invalidator.
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

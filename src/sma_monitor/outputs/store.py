"""Phase 5 persistence.

alerts          — one row per dispatched (or suppressed) alert
digests         — one row per assembled digest (date-keyed)
feedback        — useful/noise marks against alerts or scores
missed_events   — events the agent didn't surface (Phase 7 library-growth input)
"""
from __future__ import annotations

import json
from datetime import datetime

from ..db import connection
from ..identity import event_id

OUTPUTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    event_id            TEXT PRIMARY KEY,
    score_event_id      TEXT NOT NULL UNIQUE REFERENCES scores(event_id),
    article_event_id    TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    bucket_id           INTEGER,
    composite           REAL NOT NULL,
    severity_of_concern INTEGER,
    rendered_text       TEXT NOT NULL,
    channels_sent       TEXT,
    alerted_at          TEXT NOT NULL,
    suppressed          INTEGER NOT NULL DEFAULT 0,
    suppression_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker     ON alerts(ticker);
CREATE INDEX IF NOT EXISTS idx_alerts_alerted_at ON alerts(alerted_at);

CREATE TABLE IF NOT EXISTS digests (
    digest_id      TEXT PRIMARY KEY,
    date           TEXT NOT NULL UNIQUE,
    rendered_md    TEXT NOT NULL,
    n_events       INTEGER NOT NULL,
    used_narrative INTEGER NOT NULL DEFAULT 0,
    file_path      TEXT,
    assembled_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    event_id     TEXT PRIMARY KEY,
    target_id    TEXT NOT NULL,
    target_kind  TEXT NOT NULL,
    mark         TEXT NOT NULL,
    note         TEXT,
    marked_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_target ON feedback(target_id);
CREATE INDEX IF NOT EXISTS idx_feedback_mark   ON feedback(mark);

CREATE TABLE IF NOT EXISTS missed_events (
    event_id          TEXT PRIMARY KEY,
    ticker            TEXT,
    bucket_id_guess   INTEGER,
    description       TEXT NOT NULL,
    article_url       TEXT,
    note              TEXT,
    recorded_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_missed_ticker ON missed_events(ticker);
"""


def init_outputs_schema() -> None:
    with connection() as conn:
        conn.executescript(OUTPUTS_SCHEMA)


# --- ID helpers --------------------------------------------------------------


def alert_event_id(score_event_id: str) -> str:
    return event_id({"kind": "alert", "score_event_id": score_event_id})


def digest_event_id(date: str) -> str:
    return event_id({"kind": "digest", "date": date})


def feedback_event_id(target_id: str, mark: str, marked_at: datetime) -> str:
    return event_id({
        "kind": "feedback", "target_id": target_id,
        "mark": mark, "marked_at": marked_at.isoformat(),
    })


def missed_event_id(ticker: str | None, description: str, recorded_at: datetime) -> str:
    return event_id({
        "kind": "missed_event", "ticker": ticker,
        "description": description, "recorded_at": recorded_at.isoformat(),
    })


# --- Reads -------------------------------------------------------------------


def pick_alert_candidates(min_composite: float):
    """Scores above min_composite that don't yet have an alert row.

    Uses the most recent multipliers_version row per (article, ticker) — if
    the user re-scored at a new version, only the new score is considered.
    """
    init_outputs_schema()
    sql = """
        SELECT s.event_id        AS score_event_id,
               s.article_event_id, s.ticker, s.primary_bucket_id,
               s.composite, s.threshold_band,
               s.financial_impact, s.narrative_shift, s.time_criticality,
               s.rationale  AS scorer_rationale,
               a.title  AS article_title, a.url AS article_url,
               a.source, a.source_tier,
               r.event_id            AS red_team_event_id,
               r.bearish_thesis,
               r.severity_of_concern,
               r.matched_warning_signs,
               r.invalidator
        FROM scores s
        JOIN articles a   ON a.event_id = s.article_event_id
        LEFT JOIN red_team_passes r ON r.score_event_id = s.event_id
        WHERE s.composite >= ?
          AND NOT EXISTS (
            SELECT 1 FROM alerts al WHERE al.score_event_id = s.event_id
          )
        ORDER BY s.composite DESC
    """
    with connection() as conn:
        return conn.execute(sql, (min_composite,)).fetchall()


def recent_alerts_for_suppression(ticker: str, bucket_id: int, since_iso: str):
    """Alerts for same (ticker, bucket) within the suppression window."""
    sql = """
        SELECT composite, alerted_at FROM alerts
        WHERE ticker = ? AND bucket_id = ?
          AND alerted_at >= ?
          AND suppressed = 0
    """
    with connection() as conn:
        return conn.execute(sql, (ticker, bucket_id, since_iso)).fetchall()


def save_alert(
    *,
    score_event_id: str,
    article_event_id: str,
    ticker: str,
    bucket_id: int,
    composite: float,
    severity_of_concern: int | None,
    rendered_text: str,
    channels_sent: list[str],
    alerted_at: datetime,
    suppressed: bool,
    suppression_reason: str | None,
) -> str:
    init_outputs_schema()
    eid = alert_event_id(score_event_id)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "alert", ticker, alerted_at.isoformat(),
             json.dumps({"composite": composite, "suppressed": suppressed})),
        )
        conn.execute(
            """INSERT OR IGNORE INTO alerts
               (event_id, score_event_id, article_event_id, ticker, bucket_id,
                composite, severity_of_concern, rendered_text, channels_sent,
                alerted_at, suppressed, suppression_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, score_event_id, article_event_id, ticker, bucket_id,
             composite, severity_of_concern, rendered_text,
             json.dumps(channels_sent), alerted_at.isoformat(),
             1 if suppressed else 0, suppression_reason),
        )
    return eid


def save_digest(
    *,
    date: str,
    rendered_md: str,
    n_events: int,
    used_narrative: bool,
    file_path: str | None,
    assembled_at: datetime,
) -> str:
    init_outputs_schema()
    did = digest_event_id(date)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (did, "digest", None, assembled_at.isoformat(),
             json.dumps({"date": date, "n_events": n_events})),
        )
        conn.execute(
            "INSERT OR REPLACE INTO digests "
            "(digest_id, date, rendered_md, n_events, used_narrative, file_path, assembled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (did, date, rendered_md, n_events,
             1 if used_narrative else 0, file_path, assembled_at.isoformat()),
        )
    return did


def recent_alerts(*, ticker: str | None = None, limit: int = 30, include_suppressed: bool = False):
    init_outputs_schema()
    sql = "SELECT * FROM alerts WHERE 1=1"
    args: list = []
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker.upper())
    if not include_suppressed:
        sql += " AND suppressed = 0"
    sql += " ORDER BY alerted_at DESC LIMIT ?"
    args.append(limit)
    with connection() as conn:
        return conn.execute(sql, args).fetchall()


def todays_digest_events(date_iso: str):
    """All scores for one calendar day, joined with article + red team."""
    init_outputs_schema()
    sql = """
        SELECT s.event_id AS score_event_id, s.ticker, s.primary_bucket_id,
               s.composite, s.threshold_band,
               s.financial_impact, s.narrative_shift, s.time_criticality,
               s.rationale AS scorer_rationale,
               a.title, a.url, a.source, a.source_tier,
               r.bearish_thesis, r.severity_of_concern,
               r.matched_warning_signs
        FROM scores s
        JOIN articles a   ON a.event_id = s.article_event_id
        LEFT JOIN red_team_passes r ON r.score_event_id = s.event_id
        WHERE date(s.scored_at) = date(?)
        ORDER BY s.ticker, s.composite DESC
    """
    with connection() as conn:
        return conn.execute(sql, (date_iso,)).fetchall()


def bucket_ticker_hits_today(date_iso: str) -> list[tuple[int, int]]:
    """Top buckets by distinct-ticker count for today (digest concentrations)."""
    sql = """
        SELECT primary_bucket_id AS bucket_id, COUNT(DISTINCT ticker) AS n
        FROM scores
        WHERE date(scored_at) = date(?)
        GROUP BY primary_bucket_id
        ORDER BY n DESC, bucket_id ASC
    """
    with connection() as conn:
        rows = conn.execute(sql, (date_iso,)).fetchall()
    return [(r["bucket_id"], r["n"]) for r in rows]


def save_feedback(
    *,
    target_id: str,
    target_kind: str,
    mark: str,
    note: str | None,
    marked_at: datetime,
) -> str:
    init_outputs_schema()
    fid = feedback_event_id(target_id, mark, marked_at)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (fid, "feedback", None, marked_at.isoformat(),
             json.dumps({"target_id": target_id, "mark": mark})),
        )
        conn.execute(
            "INSERT OR REPLACE INTO feedback "
            "(event_id, target_id, target_kind, mark, note, marked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fid, target_id, target_kind, mark, note, marked_at.isoformat()),
        )
    return fid


def save_missed(
    *,
    ticker: str | None,
    bucket_id_guess: int | None,
    description: str,
    article_url: str | None,
    note: str | None,
    recorded_at: datetime,
) -> str:
    init_outputs_schema()
    mid = missed_event_id(ticker, description, recorded_at)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, "missed_event", ticker, recorded_at.isoformat(),
             json.dumps({"bucket": bucket_id_guess, "description": description[:120]})),
        )
        conn.execute(
            "INSERT OR IGNORE INTO missed_events "
            "(event_id, ticker, bucket_id_guess, description, article_url, note, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mid, ticker, bucket_id_guess, description, article_url, note,
             recorded_at.isoformat()),
        )
    return mid


def list_feedback(limit: int = 50):
    init_outputs_schema()
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM feedback ORDER BY marked_at DESC LIMIT ?", (limit,)
        ).fetchall()


def list_missed(limit: int = 50):
    init_outputs_schema()
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM missed_events ORDER BY recorded_at DESC LIMIT ?", (limit,)
        ).fetchall()

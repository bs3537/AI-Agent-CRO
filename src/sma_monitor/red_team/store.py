"""Phase 4 persistence.

red_team_passes — one row per (score_event_id, catalog_version). Bumping
catalog_version causes a fresh pass over every above-T₂ score. Old passes
remain in the DB for audit.
"""
from __future__ import annotations

import json
from datetime import datetime

from ..db import connection
from ..identity import event_id
from .schema import MatchedWarningSign, RedTeamResult

RED_TEAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS red_team_passes (
    event_id              TEXT PRIMARY KEY,
    score_event_id        TEXT NOT NULL REFERENCES scores(event_id),
    article_event_id      TEXT NOT NULL REFERENCES articles(event_id),
    ticker                TEXT NOT NULL,
    bearish_thesis        TEXT NOT NULL,
    matched_warning_signs TEXT,
    matched_buckets       TEXT,
    severity_of_concern   INTEGER NOT NULL,
    invalidator           TEXT,
    confidence            REAL NOT NULL,
    model_used            TEXT NOT NULL,
    catalog_version       TEXT NOT NULL,
    ran_at                TEXT NOT NULL,
    UNIQUE (score_event_id, catalog_version)
);

CREATE INDEX IF NOT EXISTS idx_red_team_ticker     ON red_team_passes(ticker);
CREATE INDEX IF NOT EXISTS idx_red_team_severity   ON red_team_passes(severity_of_concern);
CREATE INDEX IF NOT EXISTS idx_red_team_catalog    ON red_team_passes(catalog_version);
CREATE INDEX IF NOT EXISTS idx_red_team_ran_at     ON red_team_passes(ran_at);
"""


def init_red_team_schema() -> None:
    with connection() as conn:
        conn.executescript(RED_TEAM_SCHEMA)


def red_team_event_id(score_event_id: str, catalog_version: str) -> str:
    return event_id({
        "kind": "red_team",
        "score_event_id": score_event_id,
        "catalog_version": catalog_version,
    })


def save_red_team_pass(
    *,
    score_event_id: str,
    article_event_id: str,
    ticker: str,
    result: RedTeamResult,
    model_used: str,
    catalog_version: str,
    ran_at: datetime,
) -> str:
    init_red_team_schema()
    eid = red_team_event_id(score_event_id, catalog_version)
    matched_ws_json = json.dumps([ws.model_dump() for ws in result.matched_warning_signs])
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "red_team", ticker, ran_at.isoformat(),
             json.dumps({
                 "score_event_id": score_event_id,
                 "severity": result.severity_of_concern,
                 "n_patterns": len(result.matched_warning_signs),
             })),
        )
        conn.execute(
            """INSERT OR IGNORE INTO red_team_passes
               (event_id, score_event_id, article_event_id, ticker,
                bearish_thesis, matched_warning_signs, matched_buckets,
                severity_of_concern, invalidator, confidence,
                model_used, catalog_version, ran_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, score_event_id, article_event_id, ticker,
                result.bearish_thesis, matched_ws_json,
                json.dumps(result.matched_buckets),
                result.severity_of_concern, result.invalidator, result.confidence,
                model_used, catalog_version, ran_at.isoformat(),
            ),
        )
    return eid


def pick_candidates(t2: float, catalog_version: str, limit: int | None = None):
    """Scores above T₂ that have no red-team pass at this catalog_version."""
    init_red_team_schema()
    sql = """
        SELECT s.event_id        AS score_event_id,
               s.article_event_id, s.ticker, s.primary_bucket_id,
               s.composite, s.threshold_band,
               s.financial_impact, s.narrative_shift, s.time_criticality,
               s.rationale  AS scorer_rationale,
               s.confidence AS scorer_confidence,
               a.title, a.excerpt, a.source, a.source_tier, a.published_at
        FROM scores s
        JOIN articles a ON a.event_id = s.article_event_id
        WHERE s.composite >= ?
          AND NOT EXISTS (
              SELECT 1 FROM red_team_passes r
              WHERE r.score_event_id = s.event_id
                AND r.catalog_version = ?
          )
        ORDER BY s.composite DESC
    """
    args: list = [t2, catalog_version]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    with connection() as conn:
        return conn.execute(sql, args).fetchall()


def recent_passes(
    *,
    ticker: str | None = None,
    min_severity: int | None = None,
    limit: int = 30,
):
    init_red_team_schema()
    sql = """
        SELECT r.*, s.composite, s.threshold_band, s.primary_bucket_id,
               a.title, a.url
        FROM red_team_passes r
        JOIN scores s   ON s.event_id  = r.score_event_id
        JOIN articles a ON a.event_id = r.article_event_id
        WHERE 1=1
    """
    args: list = []
    if ticker:
        sql += " AND r.ticker = ?"
        args.append(ticker.upper())
    if min_severity is not None:
        sql += " AND r.severity_of_concern >= ?"
        args.append(min_severity)
    sql += " ORDER BY r.severity_of_concern DESC, s.composite DESC, r.ran_at DESC LIMIT ?"
    args.append(limit)
    with connection() as conn:
        return conn.execute(sql, args).fetchall()


def library_coverage(catalog_version: str) -> dict[str, int]:
    """How many times each warning-sign id has been cited at this catalog_version."""
    init_red_team_schema()
    sql = """
        SELECT matched_warning_signs FROM red_team_passes
        WHERE catalog_version = ?
    """
    counts: dict[str, int] = {}
    with connection() as conn:
        rows = conn.execute(sql, (catalog_version,)).fetchall()
    for r in rows:
        try:
            items = json.loads(r["matched_warning_signs"] or "[]")
        except json.JSONDecodeError:
            continue
        for item in items:
            wid = item.get("id")
            if wid:
                counts[wid] = counts.get(wid, 0) + 1
    return counts

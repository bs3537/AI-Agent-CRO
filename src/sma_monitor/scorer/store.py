"""Phase 3 SQLite persistence.

scores — one row per (article, ticker, multipliers_version). Old scores are
retained for audit when MULTIPLIERS_VERSION bumps; latest_score() returns
the most recent version per (article, ticker).

inputs_hash captures everything that should trigger a re-score: the
article identity, the holding's pct_nav/tier/stage/nearest_catalyst, the
primary bucket, and the multipliers version. When any of those change, the
hash changes and the pipeline emits a fresh score row.
"""
from __future__ import annotations

import json
from datetime import datetime

from ..db import connection
from ..identity import event_id
from .schema import CompositeScore

SCORES_SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    event_id            TEXT PRIMARY KEY,
    article_event_id    TEXT NOT NULL REFERENCES articles(event_id),
    ticker              TEXT NOT NULL,
    primary_bucket_id   INTEGER NOT NULL,
    secondary_buckets   TEXT,
    financial_impact    REAL NOT NULL,
    narrative_shift     REAL NOT NULL,
    time_criticality    REAL NOT NULL,
    raw_avg             REAL NOT NULL,
    bucket_weight       REAL NOT NULL,
    position_weight     REAL NOT NULL,
    conviction_mult     REAL NOT NULL,
    catalyst_boost      REAL NOT NULL,
    stage_interaction   REAL NOT NULL,
    composite           REAL NOT NULL,
    threshold_band      TEXT NOT NULL,
    rationale           TEXT,
    confidence          REAL NOT NULL,
    model_used          TEXT NOT NULL,
    multipliers_version TEXT NOT NULL,
    inputs_hash         TEXT NOT NULL,
    scored_at           TEXT NOT NULL,
    UNIQUE (article_event_id, ticker, multipliers_version)
);

CREATE INDEX IF NOT EXISTS idx_scores_ticker         ON scores(ticker);
CREATE INDEX IF NOT EXISTS idx_scores_band           ON scores(threshold_band);
CREATE INDEX IF NOT EXISTS idx_scores_composite      ON scores(composite);
CREATE INDEX IF NOT EXISTS idx_scores_scored_at      ON scores(scored_at);
CREATE INDEX IF NOT EXISTS idx_scores_inputs_hash    ON scores(inputs_hash);
"""


def init_scores_schema() -> None:
    with connection() as conn:
        conn.executescript(SCORES_SCHEMA)


def inputs_hash(
    *,
    article_event_id: str,
    ticker: str,
    primary_bucket_id: int,
    pct_nav: float,
    conviction_tier: int,
    stage: str,
    nearest_catalyst_days: int | None,
    multipliers_version: str,
) -> str:
    return event_id({
        "kind": "score_inputs",
        "article_event_id": article_event_id,
        "ticker": ticker,
        "primary_bucket_id": primary_bucket_id,
        "pct_nav": round(pct_nav, 4),
        "conviction_tier": conviction_tier,
        "stage": stage,
        "nearest_catalyst_days": nearest_catalyst_days,
        "multipliers_version": multipliers_version,
    })


def score_event_id(article_event_id: str, ticker: str, inputs_hash_str: str) -> str:
    return event_id({
        "kind": "score",
        "article_event_id": article_event_id,
        "ticker": ticker,
        "inputs_hash": inputs_hash_str,
    })


def save_score(score: CompositeScore) -> str:
    """Idempotent on (article, ticker, multipliers_version)."""
    init_scores_schema()
    eid = score_event_id(score.article_event_id, score.ticker, score.inputs_hash)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "score", score.ticker, score.scored_at.isoformat(),
             json.dumps({
                 "article_event_id": score.article_event_id,
                 "composite": score.composite,
                 "band": score.threshold_band,
             })),
        )
        conn.execute(
            """INSERT OR IGNORE INTO scores
               (event_id, article_event_id, ticker, primary_bucket_id, secondary_buckets,
                financial_impact, narrative_shift, time_criticality, raw_avg,
                bucket_weight, position_weight, conviction_mult, catalyst_boost,
                stage_interaction, composite, threshold_band,
                rationale, confidence, model_used, multipliers_version, inputs_hash,
                scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, score.article_event_id, score.ticker, score.primary_bucket_id,
                json.dumps(score.secondary_buckets),
                score.financial_impact, score.narrative_shift, score.time_criticality,
                score.raw_avg, score.bucket_weight, score.position_weight,
                score.conviction_mult, score.catalyst_boost, score.stage_interaction,
                score.composite, score.threshold_band,
                score.rationale, score.confidence, score.model_used,
                score.multipliers_version, score.inputs_hash,
                score.scored_at.isoformat(),
            ),
        )
    return eid


def unscored_pairs(multipliers_version: str, limit: int | None = None):
    """Article × ticker pairs that lack a score row at the current multipliers_version.

    Returns rows with the primary bucket precomputed (highest-confidence tag).
    """
    init_scores_schema()
    sql = """
        SELECT a.event_id        AS article_event_id,
               a.url, a.title, a.excerpt, a.source, a.source_tier,
               a.published_at, a.fetched_at,
               t.ticker,
               (SELECT bucket_id FROM article_buckets ab
                WHERE ab.event_id = a.event_id
                ORDER BY ab.confidence DESC LIMIT 1)   AS primary_bucket_id,
               (SELECT confidence FROM article_buckets ab
                WHERE ab.event_id = a.event_id
                ORDER BY ab.confidence DESC LIMIT 1)   AS primary_bucket_confidence
        FROM articles a
        JOIN article_tickers t ON t.event_id = a.event_id
        WHERE NOT EXISTS (
            SELECT 1 FROM scores s
            WHERE s.article_event_id = a.event_id
              AND s.ticker = t.ticker
              AND s.multipliers_version = ?
        )
          AND EXISTS (
            SELECT 1 FROM article_buckets ab WHERE ab.event_id = a.event_id
          )
        ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
    """
    args: list = [multipliers_version]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    with connection() as conn:
        return conn.execute(sql, args).fetchall()


def secondary_buckets_for(article_event_id: str, exclude_bucket_id: int):
    with connection() as conn:
        return conn.execute(
            "SELECT bucket_id, confidence FROM article_buckets "
            "WHERE event_id = ? AND bucket_id != ? ORDER BY confidence DESC",
            (article_event_id, exclude_bucket_id),
        ).fetchall()


def recent_scores(
    *,
    ticker: str | None = None,
    band: str | None = None,
    min_composite: float | None = None,
    limit: int = 50,
):
    init_scores_schema()
    sql = """
        SELECT s.*, a.title, a.url, a.source_tier
        FROM scores s
        JOIN articles a ON a.event_id = s.article_event_id
        WHERE 1=1
    """
    args: list = []
    if ticker:
        sql += " AND s.ticker = ?"
        args.append(ticker.upper())
    if band:
        sql += " AND s.threshold_band = ?"
        args.append(band)
    if min_composite is not None:
        sql += " AND s.composite >= ?"
        args.append(min_composite)
    sql += " ORDER BY s.composite DESC, s.scored_at DESC LIMIT ?"
    args.append(limit)
    with connection() as conn:
        return conn.execute(sql, args).fetchall()

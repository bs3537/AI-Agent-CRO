"""Phase 2 SQLite persistence.

Tables:
  articles         — one row per article (event_id = sha of url+title+lede)
  article_tickers  — many-to-many (article ↔ holding) — same article can
                     be relevant to multiple positions
  article_buckets  — many-to-many (article ↔ factor bucket) + confidence
  news_polls       — one row per (ticker, bucket) poll attempt; powers the
                     Phase 5 digest "bucket coverage" audit

Dedup happens at the article event_id layer. An article seen via two
different (ticker, bucket) queries inserts one articles row and adds new
edges to article_tickers / article_buckets — never a duplicate top-level row.
"""
from __future__ import annotations

import json
from datetime import datetime

from ..db import connection
from ..identity import event_id

# DDL for the Phase 2 tables. The many-to-many tables let one article live
# under multiple tickers and buckets without duplicate top-level rows.
NEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    event_id      TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    excerpt       TEXT,
    source        TEXT,
    source_tier   INTEGER,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    raw_json      TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_published   ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_source_tier ON articles(source_tier);

CREATE TABLE IF NOT EXISTS article_tickers (
    event_id   TEXT NOT NULL REFERENCES articles(event_id),
    ticker     TEXT NOT NULL,
    PRIMARY KEY (event_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_article_tickers_ticker ON article_tickers(ticker);

CREATE TABLE IF NOT EXISTS article_buckets (
    event_id    TEXT NOT NULL REFERENCES articles(event_id),
    bucket_id   INTEGER NOT NULL,
    confidence  REAL NOT NULL,
    PRIMARY KEY (event_id, bucket_id)
);
CREATE INDEX IF NOT EXISTS idx_article_buckets_bucket ON article_buckets(bucket_id);

CREATE TABLE IF NOT EXISTS news_polls (
    poll_id      TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    ticker       TEXT,
    bucket_id    INTEGER,
    query_text   TEXT NOT NULL,
    n_results    INTEGER,
    n_new        INTEGER,
    status       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_polls_started ON news_polls(started_at);
"""


# Create the Phase 2 tables. Safe to call repeatedly.
def init_news_schema() -> None:
    with connection() as conn:
        conn.executescript(NEWS_SCHEMA)


# Stable id for an article. Same url+title+lede always produces the same id
# so the pipeline can dedup re-discovered articles transparently.
def article_event_id(url: str, title: str, lede: str) -> str:
    """sha256 of url+title+lede. Idempotent dedup hinge."""
    return event_id({"url": url, "title": title, "lede": lede})


# Stable id for a poll attempt — keyed on the start time so duplicate
# attempts at the same second won't write two rows.
def poll_event_id(ticker: str | None, bucket_id: int | None, started_at: datetime) -> str:
    return event_id({
        "kind": "news_poll",
        "ticker": ticker,
        "bucket_id": bucket_id,
        "started_at": started_at.isoformat(),
    })


# Upsert one article and its (ticker, bucket) edges in a single transaction.
# Returns (event_id, is_new) so the pipeline can count new-vs-rediscovered hits.
def save_article(
    *,
    url: str,
    title: str,
    excerpt: str,
    source: str | None,
    source_tier: int | None,
    published_at: datetime | None,
    fetched_at: datetime,
    tickers: list[str],
    bucket_tags: list[tuple[int, float]],
    raw_json: str | None = None,
) -> tuple[str, bool]:
    """Upsert one article + its edges. Returns (event_id, is_new)."""
    eid = article_event_id(url, title, excerpt)
    pub_iso = published_at.isoformat() if published_at else None
    with connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM articles WHERE event_id = ?", (eid,)
        ).fetchone()
        is_new = existing is None

        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                eid,
                "article",
                tickers[0] if tickers else None,
                fetched_at.isoformat(),
                json.dumps({"url": url, "title": title}),
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO articles "
            "(event_id, url, title, excerpt, source, source_tier, "
            " published_at, fetched_at, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, url, title, excerpt, source, source_tier, pub_iso,
             fetched_at.isoformat(), raw_json),
        )
        for t in tickers:
            conn.execute(
                "INSERT OR IGNORE INTO article_tickers(event_id, ticker) VALUES (?, ?)",
                (eid, t),
            )
        for bid, conf in bucket_tags:
            # If we re-see this article with a different confidence (e.g. a
            # later query had richer text), keep the higher one.
            conn.execute(
                "INSERT INTO article_buckets(event_id, bucket_id, confidence) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(event_id, bucket_id) DO UPDATE SET "
                "confidence = MAX(confidence, excluded.confidence)",
                (eid, bid, conf),
            )
    return eid, is_new


# Record one poll attempt (ok or error). Feeds the Phase 5 coverage audit
# and Phase 7 silent-bucket detection.
def save_poll_record(
    *,
    ticker: str | None,
    bucket_id: int | None,
    query_text: str,
    started_at: datetime,
    ended_at: datetime,
    n_results: int,
    n_new: int,
    status: str,
) -> str:
    pid = poll_event_id(ticker, bucket_id, started_at)
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, "news_poll", ticker, started_at.isoformat(),
             json.dumps({"bucket_id": bucket_id, "query_text": query_text})),
        )
        conn.execute(
            "INSERT OR REPLACE INTO news_polls "
            "(poll_id, started_at, ended_at, ticker, bucket_id, "
            " query_text, n_results, n_new, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, started_at.isoformat(), ended_at.isoformat(),
             ticker, bucket_id, query_text, n_results, n_new, status),
        )
    return pid


# Read recent articles for the CLI's `show` command. Optional filters on
# ticker and bucket_id; returns sqlite rows with joined ticker/bucket lists.
def recent_articles(
    *,
    ticker: str | None = None,
    bucket_id: int | None = None,
    limit: int = 50,
):
    """For CLI show command. Returns sqlite rows with joined ticker/bucket lists."""
    init_news_schema()
    sql = """
        SELECT a.*,
               (SELECT GROUP_CONCAT(DISTINCT t.ticker)
                FROM article_tickers t WHERE t.event_id = a.event_id) AS tickers,
               (SELECT GROUP_CONCAT(b.bucket_id || ':' || ROUND(b.confidence, 2), ',')
                FROM article_buckets b WHERE b.event_id = a.event_id) AS buckets
        FROM articles a
    """
    where: list[str] = []
    args: list = []
    if ticker:
        where.append(
            "a.event_id IN (SELECT event_id FROM article_tickers WHERE ticker = ?)"
        )
        args.append(ticker.upper())
    if bucket_id is not None:
        where.append(
            "a.event_id IN (SELECT event_id FROM article_buckets WHERE bucket_id = ?)"
        )
        args.append(bucket_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT ?"
    args.append(limit)
    with connection() as conn:
        return conn.execute(sql, args).fetchall()


# Count distinct articles per bucket over the last N days. Feeds the Phase 5
# digest coverage audit and Phase 7's silent-bucket review.
def bucket_activity(days: int = 14) -> dict[int, int]:
    """Distinct articles per bucket over the last N days. Feeds Phase 5 audit."""
    init_news_schema()
    sql = """
        SELECT b.bucket_id, COUNT(DISTINCT b.event_id) AS n
        FROM article_buckets b
        JOIN articles a ON a.event_id = b.event_id
        WHERE COALESCE(a.published_at, a.fetched_at) >= datetime('now', ?)
        GROUP BY b.bucket_id
    """
    with connection() as conn:
        rows = conn.execute(sql, (f"-{days} days",)).fetchall()
    return {r["bucket_id"]: r["n"] for r in rows}

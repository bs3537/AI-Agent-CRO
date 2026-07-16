"""Manual holding deletion.

This is an operator override for cases where the latest broker pull is stale or
wrong. It deletes the current holding row plus ticker-owned monitor artifacts.
Articles are shared across tickers, so only orphaned article records are removed
after the ticker edge is deleted.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..analyst_targets.store import init_analyst_target_schema
from ..db import connection, init_db
from ..decision.store import init_decision_schema
from ..news.fmp_client import init_fmp_schema
from ..news.store import init_news_schema
from ..orchestrator.store import init_orchestrator_schema
from ..red_team.store import init_red_team_schema
from ..scorer.store import init_scores_schema
from .store import init_portfolio_schema
from .uploads import delete_file, init_uploads_schema, list_files


def delete_holding_data(ticker: str) -> dict:
    """Delete one ticker's dashboard/monitor data and return affected counts."""
    _init_delete_schemas()
    ticker = ticker.strip().upper()
    counts: dict[str, int] = {}

    file_ids = [r["event_id"] for r in list_files(ticker)]
    counts["position_files"] = sum(1 for eid in file_ids if delete_file(eid))

    with connection() as conn:
        article_ids = _ids(conn, "SELECT event_id FROM article_tickers WHERE ticker = ?", ticker)
        score_ids = _ids(conn, "SELECT event_id FROM scores WHERE ticker = ?", ticker)
        red_ids = _red_team_ids(conn, ticker, score_ids)
        decision_ids = _ids(
            conn,
            "SELECT event_id FROM position_decisions WHERE ticker = ?",
            ticker,
        )
        rating_ids = _ids(conn, "SELECT event_id FROM position_ratings WHERE ticker = ?", ticker)
        fmp_ids = _ids(conn, "SELECT event_id FROM fmp_snapshots WHERE ticker = ?", ticker)
        price_ids = _ids(conn, "SELECT event_id FROM price_series WHERE ticker = ?", ticker)
        target_ids = _ids(
            conn,
            "SELECT event_id FROM analyst_price_targets WHERE ticker = ?",
            ticker,
        )
        poll_ids = _ids(conn, "SELECT poll_id FROM news_polls WHERE ticker = ?", ticker)
        dead_ids = _ids(conn, "SELECT event_id FROM dead_letters WHERE ticker = ?", ticker)
        position_ids = _ids(conn, "SELECT event_id FROM positions WHERE ticker = ?", ticker)
        manual_ids = _ids(
            conn,
            "SELECT event_id FROM manual_positions WHERE ticker = ?",
            ticker,
        )

        counts["red_team_passes"] = _delete_by_ids(conn, "red_team_passes", "event_id", red_ids)
        counts["scores"] = _delete_by_ids(conn, "scores", "event_id", score_ids)
        counts["dead_letters"] = _delete_by_ids(conn, "dead_letters", "event_id", dead_ids)
        counts["position_decisions"] = _delete_by_ids(
            conn,
            "position_decisions",
            "event_id",
            decision_ids,
        )
        counts["position_ratings"] = _delete_by_ids(
            conn,
            "position_ratings",
            "event_id",
            rating_ids,
        )
        counts["fmp_snapshots"] = _delete_by_ids(conn, "fmp_snapshots", "event_id", fmp_ids)
        counts["price_series"] = _delete_by_ids(conn, "price_series", "event_id", price_ids)
        counts["analyst_price_targets"] = _delete_by_ids(
            conn,
            "analyst_price_targets",
            "event_id",
            target_ids,
        )
        counts["news_polls"] = _delete_by_ids(conn, "news_polls", "poll_id", poll_ids)
        counts["positions"] = _delete_by_ids(conn, "positions", "event_id", position_ids)
        counts["manual_positions"] = _delete_by_ids(
            conn,
            "manual_positions",
            "event_id",
            manual_ids,
        )

        counts["article_tickers"] = conn.execute(
            "DELETE FROM article_tickers WHERE ticker = ?",
            (ticker,),
        ).rowcount

        orphan_articles = _orphan_article_ids(conn, article_ids)
        counts["article_buckets"] = _delete_by_ids(
            conn,
            "article_buckets",
            "event_id",
            orphan_articles,
        )
        counts["articles"] = _delete_by_ids(conn, "articles", "event_id", orphan_articles)

        event_ids = (
            red_ids
            + score_ids
            + dead_ids
            + decision_ids
            + rating_ids
            + fmp_ids
            + price_ids
            + target_ids
            + poll_ids
            + position_ids
            + manual_ids
            + orphan_articles
        )
        counts["events"] = _delete_by_ids(conn, "events", "event_id", event_ids)

    return {"ticker": ticker, "deleted": counts}


def _init_delete_schemas() -> None:
    init_db()
    init_portfolio_schema()
    init_news_schema()
    init_scores_schema()
    init_red_team_schema()
    init_decision_schema()
    init_fmp_schema()
    init_analyst_target_schema()
    init_orchestrator_schema()
    init_uploads_schema()


def _ids(conn, sql: str, *args) -> list[str]:
    return [r[0] for r in conn.execute(sql, args).fetchall()]


def _red_team_ids(conn, ticker: str, score_ids: Sequence[str]) -> list[str]:
    if not score_ids:
        return _ids(conn, "SELECT event_id FROM red_team_passes WHERE ticker = ?", ticker)
    placeholders = _placeholders(score_ids)
    rows = conn.execute(
        f"""SELECT event_id FROM red_team_passes
            WHERE ticker = ? OR score_event_id IN ({placeholders})""",
        (ticker, *score_ids),
    ).fetchall()
    return [r[0] for r in rows]


def _orphan_article_ids(conn, article_ids: Sequence[str]) -> list[str]:
    orphans: list[str] = []
    for chunk in _chunks(article_ids):
        placeholders = _placeholders(chunk)
        rows = conn.execute(
            f"""SELECT a.event_id
                FROM articles a
                LEFT JOIN article_tickers t ON t.event_id = a.event_id
                WHERE a.event_id IN ({placeholders})
                GROUP BY a.event_id
                HAVING COUNT(t.ticker) = 0""",
            tuple(chunk),
        ).fetchall()
        orphans.extend(r[0] for r in rows)
    return orphans


def _delete_by_ids(conn, table: str, column: str, ids: Sequence[str]) -> int:
    deleted = 0
    for chunk in _chunks(ids):
        placeholders = _placeholders(chunk)
        deleted += conn.execute(
            f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
            tuple(chunk),
        ).rowcount
    return deleted


def _chunks(items: Sequence[str], size: int = 500):
    for i in range(0, len(items), size):
        chunk = items[i:i + size]
        if chunk:
            yield chunk


def _placeholders(items: Sequence[str]) -> str:
    return ",".join("?" for _ in items)

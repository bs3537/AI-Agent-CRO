"""Bucket-level review — PLAN.MD §7 questions.

  - Silent buckets (zero articles in window) → ingestion-gap suspicion
  - Noise-only buckets (precision << signal) → weight reduction candidate
  - Bucket #10 (literature) overlap with #1 (clinical) + #5 (competitive)
    — if >70% of #10 articles also tag into #1 or #5, folding is suggested
  - Bucket #11 (policy) cluster-with-all-tickers — if most #11 articles
    are tagged to every holding (via per-holding fallback or genuinely),
    that's a portfolio-overlay shape, not a per-position one
  - Bucket #8 (governance) cyber/breach keyword density — promote candidate
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from ..db import connection
from ..news.buckets import load_buckets

# Keyword set used to detect cyber/breach articles inside bucket #8. Drives
# the "should cyber be its own bucket?" PLAN §7 question.
CYBER_BREACH_TERMS = ("cyber", "breach", "ransomware", "data leak", "data breach")


# Return the bucket ids that saw zero articles in the window — probable
# ingestion gaps. Used by the bucket-review section of the tuning report.
def silent_buckets(*, days: int = 14) -> list[int]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT DISTINCT b.bucket_id
        FROM article_buckets b
        JOIN articles a ON a.event_id = b.event_id
        WHERE COALESCE(a.published_at, a.fetched_at) >= ?
    """
    with connection() as conn:
        rows = conn.execute(sql, (since,)).fetchall()
    active = {r["bucket_id"] for r in rows}
    return sorted(set(load_buckets()) - active)


# Compute the fraction of bucket-#10 (literature) articles in the window
# that also carry a #1 or #5 tag. ≥70% suggests #10 is folding into the
# clinical/competitive read-through buckets — PLAN §7 4-week decision.
def bucket_10_overlap(*, days: int = 28) -> dict:
    """% of bucket-10 articles in window also tagged into #1 or #5."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT a.event_id, GROUP_CONCAT(ab.bucket_id) AS bids
        FROM articles a
        JOIN article_buckets ab ON ab.event_id = a.event_id
        WHERE COALESCE(a.published_at, a.fetched_at) >= ?
        GROUP BY a.event_id
        HAVING SUM(CASE WHEN ab.bucket_id = 10 THEN 1 ELSE 0 END) > 0
    """
    with connection() as conn:
        rows = conn.execute(sql, (since,)).fetchall()
    total_10 = len(rows)
    overlap = sum(
        1 for r in rows
        if r["bids"] and any(b in r["bids"].split(",") for b in ("1", "5"))
    )
    pct = (overlap / total_10) if total_10 > 0 else 0.0
    return {
        "total_10_articles": total_10,
        "overlap_with_1_or_5": overlap,
        "overlap_pct": round(pct, 3),
        "fold_suggestion": (
            "Yes — consider folding #10 into #1+#5 (overlap >70%)." if pct >= 0.7 and total_10 >= 5
            else f"No — overlap {pct*100:.0f}% over {total_10} articles."
        ),
    }


# Average distinct tickers per bucket-#11 article. When this approaches
# the total holding count, #11 is behaving like a sector overlay — PLAN §7
# 8-week decision point.
def bucket_11_cluster_shape(*, days: int = 56) -> dict:
    """Are #11 articles consistently tagged across MOST holdings?

    Heuristic: avg tickers-per-#11-article over (last 8 weeks). If avg
    approaches the total number of current holdings, #11 is behaving
    like a sector overlay (PLAN §7 8-week decision point).
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT a.event_id, COUNT(DISTINCT t.ticker) AS n_tickers
        FROM articles a
        JOIN article_buckets ab ON ab.event_id = a.event_id AND ab.bucket_id = 11
        LEFT JOIN article_tickers t ON t.event_id = a.event_id
        WHERE COALESCE(a.published_at, a.fetched_at) >= ?
        GROUP BY a.event_id
    """
    with connection() as conn:
        rows = conn.execute(sql, (since,)).fetchall()
    from ..portfolio.joined import latest_joined
    holdings, _missing, _ = latest_joined()
    n_holdings = len(holdings) or 1

    n_articles = len(rows)
    if n_articles == 0:
        return {
            "n_articles": 0,
            "avg_tickers_per_article": None,
            "n_holdings": n_holdings,
            "ratio": None,
            "overlay_suggestion": "No #11 articles in window — defer the decision.",
        }
    avg = sum(r["n_tickers"] for r in rows) / n_articles
    ratio = avg / n_holdings
    suggestion = (
        f"Yes — #11 articles touch {avg:.1f}/{n_holdings} tickers on average "
        f"({ratio*100:.0f}%). Strong portfolio-overlay shape."
        if ratio >= 0.75 and n_articles >= 5
        else f"Not yet — ratio {ratio*100:.0f}% over {n_articles} articles."
    )
    return {
        "n_articles": n_articles,
        "avg_tickers_per_article": round(avg, 2),
        "n_holdings": n_holdings,
        "ratio": round(ratio, 3),
        "overlay_suggestion": suggestion,
    }


# Fraction of bucket-#8 (governance) articles in the window that mention
# cyber/breach terms. ≥40% triggers a "promote to own bucket" suggestion.
def cyber_breach_density(*, days: int = 60) -> dict:
    """#8 articles with cyber/breach terms — PLAN §7 own-bucket candidate."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT a.title, a.excerpt
        FROM articles a
        JOIN article_buckets b ON b.event_id = a.event_id AND b.bucket_id = 8
        WHERE COALESCE(a.published_at, a.fetched_at) >= ?
    """
    with connection() as conn:
        rows = conn.execute(sql, (since,)).fetchall()
    total = len(rows)
    cyber = 0
    for r in rows:
        text = ((r["title"] or "") + " " + (r["excerpt"] or "")).lower()
        if any(term in text for term in CYBER_BREACH_TERMS):
            cyber += 1
    pct = (cyber / total) if total > 0 else 0.0
    if total < 5:
        suggestion = (f"Defer — only {total} #8 articles in window, insufficient to judge.")
    elif pct >= 0.40:
        suggestion = (
            f"Yes — {cyber}/{total} ({pct*100:.0f}%) of #8 articles are "
            f"cyber/breach-related; consider promoting to its own bucket."
        )
    else:
        suggestion = (
            f"Not yet — {cyber}/{total} ({pct*100:.0f}%) cyber/breach share; "
            f"keep folded in #8."
        )
    return {"total_8_articles": total, "cyber_articles": cyber,
            "cyber_pct": round(pct, 3), "promotion_suggestion": suggestion}


# Render the bucket-review section of the tuning report: silent buckets +
# the three PLAN §7 architectural questions with their current answers.
def render_bucket_review_markdown(
    silent: Sequence[int],
    overlap_10: dict,
    cluster_11: dict,
    cyber_8: dict,
    *,
    days: int,
) -> str:
    buckets = load_buckets()
    parts: list[str] = []
    parts.append(f"*Window: last {days} days.*")
    parts.append("")
    parts.append("**Silent buckets** (0 articles in window — probable ingestion gap):")
    if not silent:
        parts.append("- *(none — every bucket had at least one article)*")
    else:
        for bid in silent:
            name = buckets[bid].name if bid in buckets else "?"
            parts.append(f"- #{bid} ({name})")
    parts.append("")

    parts.append("**PLAN §7 question: Is #10 (literature) folding into #1 + #5?**")
    parts.append(f"- {overlap_10['fold_suggestion']}")
    parts.append(f"- (raw: {overlap_10['overlap_with_1_or_5']} of "
                 f"{overlap_10['total_10_articles']} #10 articles also tagged #1 or #5)")
    parts.append("")

    parts.append("**PLAN §7 question: Should #11 (policy) move to portfolio overlay?**")
    parts.append(f"- {cluster_11['overlay_suggestion']}")
    if cluster_11["avg_tickers_per_article"] is not None:
        parts.append(f"- (raw: {cluster_11['n_articles']} #11 articles, "
                     f"avg {cluster_11['avg_tickers_per_article']:.1f} tickers each, "
                     f"{cluster_11['n_holdings']} holdings in portfolio)")
    parts.append("")

    parts.append("**PLAN §7 question: Is cyber/breach earning its own bucket out of #8?**")
    parts.append(f"- {cyber_8['promotion_suggestion']}")
    parts.append(f"- (raw: {cyber_8['cyber_articles']} / {cyber_8['total_8_articles']} "
                 f"#8 articles match cyber/breach keywords)")
    return "\n".join(parts)

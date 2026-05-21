"""Per-bucket alert precision (PLAN.MD §7).

Joins `alerts` × `feedback` per bucket. A feedback row can target either the
alert event_id or the underlying score_event_id — we count both. Precision is
useful / (useful + noise); we don't punish unmarked alerts because the user
may not have gotten to them yet. We DO surface `mark_rate` separately so a
low mark_rate is visible (likely a feedback-discipline issue, not a quality
signal).

A bucket with fewer than MIN_MARKED_FOR_SIGNAL marks is reported with
'insufficient_data' so the weight tuner doesn't act on noise.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from ..db import connection

# Minimum marked-alerts a bucket needs before precision is treated as a
# reliable signal. Below this, the weight tuner refuses to suggest changes.
MIN_MARKED_FOR_SIGNAL = 5


# Compute precision (useful / (useful + noise)) per bucket over a rolling
# window. Returns one row per bucket that had at least one alert in window.
def alert_precision_by_bucket(*, days: int = 14) -> list[dict]:
    """One row per bucket that had at least one alert in the window."""
    since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT
            a.bucket_id                                  AS bucket_id,
            COUNT(DISTINCT a.event_id)                   AS alerts_total,
            COUNT(DISTINCT CASE WHEN a.suppressed = 1
                                 THEN a.event_id END)    AS alerts_suppressed,
            COUNT(DISTINCT CASE WHEN f.mark = 'useful'
                                 THEN a.event_id END)    AS useful,
            COUNT(DISTINCT CASE WHEN f.mark = 'noise'
                                 THEN a.event_id END)    AS noise,
            COALESCE(AVG(a.composite), 0)                AS avg_composite
        FROM alerts a
        LEFT JOIN feedback f
            ON f.target_id IN (a.event_id, a.score_event_id)
        WHERE a.alerted_at >= ?
        GROUP BY a.bucket_id
        ORDER BY a.bucket_id ASC
    """
    out: list[dict] = []
    with connection() as conn:
        rows = conn.execute(sql, (since_iso,)).fetchall()
    for r in rows:
        marked = (r["useful"] or 0) + (r["noise"] or 0)
        unmarked = (r["alerts_total"] or 0) - marked
        precision = (r["useful"] / marked) if marked > 0 else None
        mark_rate = (marked / r["alerts_total"]) if r["alerts_total"] > 0 else 0.0
        out.append({
            "bucket_id": r["bucket_id"],
            "alerts_total": r["alerts_total"] or 0,
            "alerts_suppressed": r["alerts_suppressed"] or 0,
            "useful": r["useful"] or 0,
            "noise": r["noise"] or 0,
            "unmarked": unmarked,
            "precision": precision,
            "mark_rate": round(mark_rate, 3),
            "avg_composite": round(r["avg_composite"], 2),
            "insufficient_data": marked < MIN_MARKED_FOR_SIGNAL,
        })
    return out


# Render the precision rows as a markdown table for the tuning report.
def render_precision_markdown(rows: Sequence[dict], days: int) -> str:
    if not rows:
        return f"*No alerts in the last {days} days.*"
    parts = [
        f"*Window: last {days} days. Insufficient = fewer than {MIN_MARKED_FOR_SIGNAL} marks.*",
        "",
        f"| Bucket | Alerts | Useful | Noise | Unmarked | Precision | Mark rate | Avg comp |",
        f"|-------:|-------:|-------:|------:|---------:|----------:|----------:|---------:|",
    ]
    for r in rows:
        prec = f"{r['precision']*100:.0f}%" if r["precision"] is not None else "—"
        flag = "  ⚠ insufficient" if r["insufficient_data"] else ""
        parts.append(
            f"| #{r['bucket_id']} | {r['alerts_total']} | {r['useful']} | "
            f"{r['noise']} | {r['unmarked']} | {prec} | "
            f"{r['mark_rate']*100:.0f}% | {r['avg_composite']:.1f} |{flag}"
        )
    return "\n".join(parts)

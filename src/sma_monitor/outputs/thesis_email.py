"""Morning 9 AM ET thesis-drift email (Workstream 7).

Assembles a per-position summary of the latest thesis rating — grade, action,
open P&L, %NAV, 20-day EMA status, and the 4–5 line note — ordered D → C → B
→ A, then by portfolio priority. Reuses outputs/channels.py: the email goes out
via EmailChannel when SMTP is configured and is always archived to
data/digests/thesis/YYYY-MM-DD.md by FileChannel.

This is additive to the evening digest, not a replacement — the digest still
runs at the 9 PM dispatch. Ratings come from the W3 position_ratings store; the
morning orchestrator cycle recomputes any stale ones before calling this.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..decision.schema import GRADE_VERDICT
from ..decision.store import latest_decisions, latest_ratings
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from .channels import Channel, build_channels

log = logging.getLogger("sma_monitor.outputs.thesis_email")

# Eastern time so the email's date matches the "9 AM ET" firing regardless of
# the host's UTC offset.
ET = ZoneInfo("America/New_York")

# Ordering rank + glyph per canonical grade. Positions with no rating yet sort
# last via the default rank.
_GRADE_RANK = {"D": 0, "C": 1, "B": 2, "A": 3}
_DOT = {"D": "🔴", "C": "🟠", "B": "🟡", "A": "🟢"}


# Assemble and dispatch the morning thesis-drift email. Iterates the monitored
# holdings, attaches each one's latest decision + open P&L, orders them, renders
# markdown, and sends via every configured channel. Returns a summary dict.
def assemble_thesis_email(
    *,
    date_iso: str | None = None,
    channels: list[Channel] | None = None,
) -> dict:
    if date_iso is None:
        date_iso = datetime.now(tz=ET).date().isoformat()
    if channels is None:
        channels = build_channels(prefer_stdout=False)

    holdings, _missing, pulled_at = latest_joined()
    ratings_by_ticker = {r["ticker"]: r for r in latest_ratings()}
    decisions_by_ticker = {d["ticker"]: d for d in latest_decisions()}

    rows = [
        _row_for(h, ratings_by_ticker.get(h.ticker), decisions_by_ticker.get(h.ticker))
        for h in holdings
    ]
    rows.sort(key=_sort_key)

    subject = _subject(date_iso, rows)
    rendered = render_thesis_email_markdown(rows, date_iso=date_iso, pulled_at=pulled_at)

    file_path = None
    for ch in channels:
        try:
            res = ch.send_thesis_email(date_iso, subject, rendered)
            if res is not None:
                file_path = str(res)
        except Exception as e:
            log.error("thesis_email_channel_failed", extra={"channel": ch.name, "err": str(e)})

    counts = _grade_counts(rows)
    log.info("thesis_email_sent",
             extra={"date": date_iso, "positions": len(rows),
                    "by_grade": counts, "file": file_path})
    return {
        "date": date_iso,
        "positions": len(rows),
        "by_grade": counts,
        "by_verdict": _verdict_counts(rows),  # backward-compatible summary key
        "file_path": file_path,
        "subject": subject,
    }


# Build one render row from a holding + its (optional) latest rating row,
# computing open P&L from market_value − cost_basis.
def _row_for(h: Holding, r, d=None) -> dict:
    open_pnl = pnl_pct = None
    if h.cost_basis is not None:
        open_pnl = h.market_value - h.cost_basis
        if h.cost_basis:
            pnl_pct = open_pnl / h.cost_basis
    try:
        drivers = json.loads((r or d)["drivers"] or "[]") if (r or d) else []
    except (TypeError, json.JSONDecodeError):
        drivers = []
    grade = r["grade"] if r else _legacy_grade(d)
    action = r["action"] if r else ("sell" if grade == "D" else "hold")
    verdict = GRADE_VERDICT.get(grade, d["verdict"] if d else "none")
    return {
        "ticker": h.ticker,
        "company_name": h.company_name,
        "stage": h.stage,
        "conviction_tier": int(h.conviction_tier),
        "pct_nav": h.pct_nav,
        "open_pnl": open_pnl,
        "pnl_pct": pnl_pct,
        "nearest_catalyst_days": h.nearest_catalyst_days,
        "has_overdue_catalyst": h.has_overdue_catalyst,
        "grade": grade,
        "action": action,
        "attention_state": r["attention_state"] if r else "unknown",
        "verdict": verdict,
        "color": d["color"] if d else "none",
        "risk_score": r["risk_score"] if r else None,
        "technical_state": r["technical_state"] if r else "no_price_data",
        "price_vs_ema20_pct": r["price_vs_ema20_pct"] if r else None,
        "note": (r or d)["note"] if (r or d) else "",
        "drivers": drivers,
        "confidence": (r or d)["confidence"] if (r or d) else None,
        "decided_at": (r or d)["decided_at"] if (r or d) else None,
    }


# Count rows per grade for the subject line + log summary.
def _grade_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {"D": 0, "C": 0, "B": 0, "A": 0}
    for r in rows:
        grade = r.get("grade", "none")
        counts[grade] = counts.get(grade, 0) + 1
    return counts


# Count rows per legacy verdict for compatibility with old callers.
def _verdict_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return counts


# Subject line: leads with D/C/B/A counts so the inbox preview is glanceable.
def _subject(date_iso: str, rows: list[dict]) -> str:
    c = _grade_counts(rows)
    return (
        f"SMA thesis ratings {date_iso} — "
        f"{c.get('D', 0)} SELL D · {c.get('C', 0)} HOLD C · "
        f"{c.get('B', 0)} HOLD B · {c.get('A', 0)} HOLD A"
    )


# Format open P&L for the email line; em-dash when cost basis is unknown.
def _pnl_label(open_pnl: float | None, pnl_pct: float | None) -> str:
    if open_pnl is None:
        return "P&L —"
    pct = f"{pnl_pct * 100:+.1f}%" if pnl_pct is not None else "—"
    return f"P&L {open_pnl:+,.0f} ({pct})"


# Render the morning email as markdown (also the archived file body). One
# block per position in the pre-sorted order, each with the colored verdict,
# economics, the 4–5 line note, and driver chips.
def render_thesis_email_markdown(
    rows: list[dict],
    *,
    date_iso: str,
    pulled_at: datetime | None = None,
) -> str:
    parts: list[str] = [f"# SMA Thesis-Drift — {date_iso}", ""]
    c = _grade_counts(rows)
    parts.append(
        f"**{c.get('D', 0)} SELL D · {c.get('C', 0)} HOLD C · "
        f"{c.get('B', 0)} HOLD B · {c.get('A', 0)} HOLD A**"
        + (f" · {c['none']} no rating" if c.get("none") else "")
    )
    if pulled_at is not None:
        parts.append(f"*Positions pulled {pulled_at.isoformat()}*")
    parts.append("")

    if not rows:
        parts.append("*(no monitored positions with a sidecar)*")
        return "\n".join(parts)

    for r in rows:
        dot = _DOT.get(r.get("grade"), "⚪")
        rating_label = _rating_label(r)
        cat = (
            f", catalyst {r['nearest_catalyst_days']}d"
            if r["nearest_catalyst_days"] is not None else ""
        )
        if r["has_overdue_catalyst"]:
            cat += " ⚠overdue"
        parts.append(
            f"## {dot} {r['ticker']} — {rating_label}  "
            f"({r['pct_nav'] * 100:.1f}% NAV · {_pnl_label(r['open_pnl'], r['pnl_pct'])}{cat})"
        )
        sub = r["company_name"] or "—"
        parts.append(f"*{sub} · tier {r['conviction_tier']} · {r['stage']}*")
        parts.append(f"*{_technical_label(r)}*")
        parts.append("")
        if r["note"]:
            parts.append(r["note"].strip())
        else:
            parts.append("*(no rating computed yet — run a recompute)*")
        if r["drivers"]:
            parts.append("")
            parts.append("Drivers: " + "; ".join(r["drivers"]))
        if r["confidence"] is not None:
            parts.append(f"*confidence {r['confidence'] * 100:.0f}%*")
        parts.append("")
        parts.append("---")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _sort_key(r: dict) -> tuple:
    grade_rank = _GRADE_RANK.get(r.get("grade"), 4)
    catalyst_days = r["nearest_catalyst_days"] if r["nearest_catalyst_days"] is not None else 9999
    below_ema = 0 if r.get("technical_state") in {"below_ema20", "extended_below_ema20"} else 1
    return (grade_rank, -r["pct_nav"], catalyst_days, below_ema)


def _legacy_grade(d) -> str:
    if d is None:
        return "none"
    if d["verdict"] == "sell":
        return "D"
    if d["verdict"] == "watch":
        return "C"
    return "A"


def _rating_label(r: dict) -> str:
    grade = r.get("grade")
    if grade not in _GRADE_RANK:
        return "NO RATING"
    return f"{r.get('action', 'hold').upper()} {grade}"


def _technical_label(r: dict) -> str:
    state = r.get("technical_state") or "no_price_data"
    pct = r.get("price_vs_ema20_pct")
    label = state.replace("_", " ")
    if pct is not None:
        label += f" ({pct * 100:+.1f}% vs EMA20)"
    score = r.get("risk_score")
    if score is not None:
        label += f" · risk {score:.1f}"
    return label

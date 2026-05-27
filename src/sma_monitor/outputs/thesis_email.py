"""Morning 9 AM ET thesis-drift email (Workstream 7).

Assembles a per-position summary of the latest thesis-drift decision — color,
verdict, open P&L, %NAV, and the 4–5 line note — ordered sell → watch → hold
then by %NAV descending so the positions most under pressure (and the largest)
lead. Reuses outputs/channels.py: the email goes out via EmailChannel when SMTP
is configured and is always archived to data/digests/thesis/YYYY-MM-DD.md by
FileChannel.

This is additive to the evening digest, not a replacement — the digest still
runs at the 9 PM dispatch. The decisions themselves come from the W3
position_decisions store; the morning orchestrator cycle recomputes any stale
ones before calling this.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..decision.store import latest_decisions
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from .channels import Channel, build_channels

log = logging.getLogger("sma_monitor.outputs.thesis_email")

# Eastern time so the email's date matches the "9 AM ET" firing regardless of
# the host's UTC offset.
ET = ZoneInfo("America/New_York")

# Ordering rank + glyph per verdict (sell first; positions with no decision
# yet sort last via the default rank).
_VERDICT_RANK = {"sell": 0, "watch": 1, "hold": 2}
_DOT = {"sell": "🔴", "watch": "🟡", "hold": "🟢"}


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
    decisions_by_ticker = {d["ticker"]: d for d in latest_decisions()}

    rows = [_row_for(h, decisions_by_ticker.get(h.ticker)) for h in holdings]
    # sell → watch → hold, then largest position first.
    rows.sort(key=lambda r: (_VERDICT_RANK.get(r["verdict"], 3), -r["pct_nav"]))

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

    counts = _verdict_counts(rows)
    log.info("thesis_email_sent",
             extra={"date": date_iso, "positions": len(rows),
                    "by_verdict": counts, "file": file_path})
    return {"date": date_iso, "positions": len(rows), "by_verdict": counts,
            "file_path": file_path, "subject": subject}


# Build one render row from a holding + its (optional) latest decision row,
# computing open P&L from market_value − cost_basis.
def _row_for(h: Holding, d) -> dict:
    open_pnl = pnl_pct = None
    if h.cost_basis is not None:
        open_pnl = h.market_value - h.cost_basis
        if h.cost_basis:
            pnl_pct = open_pnl / h.cost_basis
    try:
        drivers = json.loads(d["drivers"] or "[]") if d else []
    except (TypeError, json.JSONDecodeError):
        drivers = []
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
        "verdict": d["verdict"] if d else "none",
        "color": d["color"] if d else "none",
        "note": d["note"] if d else "",
        "drivers": drivers,
        "confidence": d["confidence"] if d else None,
        "decided_at": d["decided_at"] if d else None,
    }


# Count rows per verdict for the subject line + log summary.
def _verdict_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return counts


# Subject line: leads with sell/watch counts so the inbox preview is glanceable.
def _subject(date_iso: str, rows: list[dict]) -> str:
    c = _verdict_counts(rows)
    return (f"SMA thesis-drift {date_iso} — "
            f"{c.get('sell', 0)} sell · {c.get('watch', 0)} watch · {c.get('hold', 0)} hold")


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
    c = _verdict_counts(rows)
    parts.append(
        f"**{c.get('sell', 0)} sell · {c.get('watch', 0)} watch · {c.get('hold', 0)} hold**"
        + (f" · {c['none']} no decision" if c.get("none") else "")
    )
    if pulled_at is not None:
        parts.append(f"*Positions pulled {pulled_at.isoformat()}*")
    parts.append("")

    if not rows:
        parts.append("*(no monitored positions with a sidecar)*")
        return "\n".join(parts)

    for r in rows:
        dot = _DOT.get(r["verdict"], "⚪")
        verdict = r["verdict"].upper()
        cat = (
            f", catalyst {r['nearest_catalyst_days']}d"
            if r["nearest_catalyst_days"] is not None else ""
        )
        if r["has_overdue_catalyst"]:
            cat += " ⚠overdue"
        parts.append(
            f"## {dot} {r['ticker']} — {verdict}  "
            f"({r['pct_nav'] * 100:.1f}% NAV · {_pnl_label(r['open_pnl'], r['pnl_pct'])}{cat})"
        )
        sub = r["company_name"] or "—"
        parts.append(f"*{sub} · tier {r['conviction_tier']} · {r['stage']}*")
        parts.append("")
        if r["note"]:
            parts.append(r["note"].strip())
        else:
            parts.append("*(no decision computed yet — run a recompute)*")
        if r["drivers"]:
            parts.append("")
            parts.append("Drivers: " + "; ".join(r["drivers"]))
        if r["confidence"] is not None:
            parts.append(f"*confidence {r['confidence'] * 100:.0f}%*")
        parts.append("")
        parts.append("---")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"

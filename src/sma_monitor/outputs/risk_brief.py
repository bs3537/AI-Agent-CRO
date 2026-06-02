"""Daily 9 AM ET risk brief — the Chief Risk Officer's red-team update (W7).

This supersedes the plain-markdown thesis_email.py for the scheduled morning
slot. It diffs today's live ratings against the prior session's snapshot
(decision/snapshots.py) and renders a polished, email-client-safe HTML brief
delivered via Resend (channels.ResendChannel), with a plain-text fallback.

Ordering follows the PM's standing instruction:
  1. Holdings whose rating flipped to SELL from HOLD (grade → D).
  2. Holdings still HOLD whose letter grade worsened (e.g. B → C).
  3. Stabilizing / upgrades, then newly-tracked names, then a standing watch
     list of unchanged C/D positions for context.

Pure renderers (render_risk_brief_html / _text) take already-classified buckets
so they are trivial to unit-test; assemble_risk_brief wires in the live DB.
"""
from __future__ import annotations

import html as _html
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..decision.snapshots import previous_snapshots, record_daily_snapshots
from ..decision.store import latest_ratings
from ..portfolio.joined import latest_joined
from .channels import Channel, build_channels

log = logging.getLogger("sma_monitor.outputs.risk_brief")

# Eastern time so the brief's date matches the "9 AM ET" firing regardless of
# the host's UTC offset.
ET = ZoneInfo("America/New_York")

# Worse grade → higher rank. Drives both the change classification and section
# ordering (D worst). Non-graded rows sort last via .get(grade, 0).
GRADE_RANK = {"A": 1, "B": 2, "C": 3, "D": 4}

# Per-grade pill background. Mirrors the dashboard semantics: A green (clean),
# B gold (monitor), C orange (watch), D red (broken → sell).
GRADE_BG = {"A": "#059669", "B": "#ca8a04", "C": "#ea580c", "D": "#dc2626", "none": "#94a3b8"}

# Section accent per change kind — red urgent, amber caution, green positive.
ACCENT = {"new_sell": "#dc2626", "deterioration": "#d97706", "improvement": "#059669"}

# Web-safe font stack used throughout the inline styles.
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


# Assemble and dispatch the daily risk brief. Diffs live ratings against the
# previous session's snapshot, classifies the changes, renders HTML + text, then
# records today's snapshot so tomorrow has a baseline. Returns a summary dict.
def assemble_risk_brief(
    *,
    date_iso: str | None = None,
    channels: list[Channel] | None = None,
    prefer_stdout: bool = False,
) -> dict:
    if date_iso is None:
        date_iso = datetime.now(tz=ET).date().isoformat()
    if channels is None:
        channels = build_channels(prefer_stdout=prefer_stdout)

    holdings, _missing, pulled_at = latest_joined()
    ratings_by_ticker = {r["ticker"]: r for r in latest_ratings()}
    prev_by_ticker = previous_snapshots(date_iso)

    rows = [
        _brief_row(h, ratings_by_ticker.get(h.ticker), prev_by_ticker.get(h.ticker))
        for h in holdings
    ]
    buckets = classify_changes(rows)

    subject = _subject(date_iso, buckets)
    html_body = render_risk_brief_html(buckets, rows, date_iso=date_iso, pulled_at=pulled_at)
    text_body = render_risk_brief_text(buckets, rows, date_iso=date_iso, pulled_at=pulled_at)

    # Capture today's snapshot AFTER diffing so the next run has a baseline. A
    # capture failure must not block delivery — the brief is already rendered.
    try:
        record_daily_snapshots(date_iso)
    except Exception as e:
        log.error("risk_brief_snapshot_failed", extra={"err": str(e)})

    file_path = None
    sent_channels: list[str] = []
    for ch in channels:
        try:
            res = ch.send_risk_brief(date_iso, subject, html_body, text_body)
            sent_channels.append(ch.name)
            if res is not None:
                file_path = str(res)
        except Exception as e:
            log.error("risk_brief_channel_failed", extra={"channel": ch.name, "err": str(e)})

    summary = {
        "date": date_iso,
        "positions": len(rows),
        "downgrades_to_sell": [r["ticker"] for r in buckets["downgrades_to_sell"]],
        "deteriorations": [r["ticker"] for r in buckets["deteriorations"]],
        "improvements": [r["ticker"] for r in buckets["improvements"]],
        "new_positions": [r["ticker"] for r in buckets["new_positions"]],
        "channels": sent_channels,
        "file_path": file_path,
        "subject": subject,
    }
    log.info("risk_brief_sent", extra=summary)
    return summary


# Build one render row from a holding + its latest rating row + the prior
# session's snapshot, computing open P&L and classifying the day's change.
def _brief_row(h, r, prev) -> dict:
    open_pnl = pnl_pct = None
    if h.cost_basis is not None:
        open_pnl = h.market_value - h.cost_basis
        if h.cost_basis:
            pnl_pct = open_pnl / h.cost_basis
    try:
        drivers = json.loads(r["drivers"] or "[]") if r else []
    except (TypeError, json.JSONDecodeError):
        drivers = []
    grade = r["grade"] if r else "none"
    action = r["action"] if r else "none"
    prev_grade = prev["grade"] if prev else None
    prev_action = prev["action"] if prev else None
    change_kind = _classify_kind(action, grade, prev_action, prev_grade, prev is not None)
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
        "risk_score": r["risk_score"] if r else None,
        "technical_state": r["technical_state"] if r else "no_price_data",
        "price_vs_ema20_pct": r["price_vs_ema20_pct"] if r else None,
        "note": r["note"] if r else "",
        "drivers": drivers,
        "confidence": r["confidence"] if r else None,
        "decided_at": r["decided_at"] if r else None,
        "prev_grade": prev_grade,
        "prev_action": prev_action,
        "prev_captured_at": prev["captured_at"] if prev else None,
        "has_previous": prev is not None,
        "change_kind": change_kind,
    }


# Classify one holding's day-over-day move into a change kind. "new_sell" =
# flipped to SELL from HOLD; "deterioration" = still HOLD but a worse grade;
# "improvement" = grade up or SELL→HOLD recovery; "new" = no prior snapshot.
def _classify_kind(action, grade, prev_action, prev_grade, has_previous) -> str:
    if grade not in GRADE_RANK:
        return "no_rating"
    if not has_previous or prev_grade not in GRADE_RANK:
        return "new"
    cr, pr = GRADE_RANK[grade], GRADE_RANK[prev_grade]
    if prev_action == "hold" and action == "sell":
        return "new_sell"
    if action == "hold" and prev_action == "hold" and cr > pr:
        return "deterioration"
    if cr < pr or (prev_action == "sell" and action == "hold"):
        return "improvement"
    return "none"


# Partition rows into the brief's ordered buckets. SELL flips lead (largest NAV
# first); deteriorations follow worst-grade-first; then improvements, newly
# tracked names, and a standing watch list of unchanged C/D positions.
def classify_changes(rows: list[dict]) -> dict[str, list[dict]]:
    sell = [r for r in rows if r["change_kind"] == "new_sell"]
    det = [r for r in rows if r["change_kind"] == "deterioration"]
    imp = [r for r in rows if r["change_kind"] == "improvement"]
    new = [r for r in rows if r["change_kind"] == "new"]
    sell.sort(key=lambda r: -r["pct_nav"])
    det.sort(key=lambda r: (-GRADE_RANK.get(r["grade"], 0), -r["pct_nav"]))
    imp.sort(key=lambda r: -r["pct_nav"])
    new.sort(key=lambda r: (-GRADE_RANK.get(r["grade"], 0), -r["pct_nav"]))
    changed = {id(r) for r in (*sell, *det, *imp)}
    watch = [r for r in rows if r["grade"] in {"C", "D"} and id(r) not in changed]
    watch.sort(key=lambda r: (-GRADE_RANK.get(r["grade"], 0), -r["pct_nav"]))
    return {
        "downgrades_to_sell": sell,
        "deteriorations": det,
        "improvements": imp,
        "new_positions": new,
        "watch_list": watch,
    }


# Subject line: lead with the actionable exception counts, not the whole book.
def _subject(date_iso: str, buckets: dict[str, list[dict]]) -> str:
    n_sell = len(buckets["downgrades_to_sell"])
    n_det = len(buckets["deteriorations"])
    if n_sell or n_det:
        bits = []
        if n_sell:
            bits.append(f"{n_sell} new SELL")
        if n_det:
            bits.append(f"{n_det} grade deterioration{'s' if n_det != 1 else ''}")
        return f"AI CRO Daily Risk Brief — {date_iso} · " + " · ".join(bits)
    return f"AI CRO Daily Risk Brief — {date_iso} · No rating or grade changes"


# ---------------------------------------------------------------------------
# HTML rendering helpers (inline styles only — no <style> block or external CSS
# so the brief survives Gmail/Outlook/Apple Mail stripping).
# ---------------------------------------------------------------------------

# HTML-escape any model/holding-derived text before embedding it.
def _esc(s) -> str:
    return _html.escape(str(s))


# Human label for a rating, e.g. "SELL D" / "HOLD C" / "NO RATING".
def _rating_label(action, grade) -> str:
    if grade not in GRADE_RANK:
        return "NO RATING"
    return f"{(action or 'hold').upper()} {grade}"


# A colored rating pill; muted=True renders the prior (greyed) state.
def _rating_pill(action, grade, *, muted: bool = False) -> str:
    label = _esc(_rating_label(action, grade))
    if muted:
        return (f'<span style="display:inline-block;background:#f1f5f9;color:#64748b;'
                f'border:1px solid #e2e8f0;font:600 12px/1 {FONT};padding:5px 9px;'
                f'border-radius:6px;white-space:nowrap;">{label}</span>')
    bg = GRADE_BG.get(grade, "#94a3b8")
    return (f'<span style="display:inline-block;background:{bg};color:#ffffff;'
            f'font:700 12px/1 {FONT};padding:5px 9px;border-radius:6px;'
            f'white-space:nowrap;">{label}</span>')


# "PREV → CURR" transition built from two pills.
def _transition(r: dict) -> str:
    prev = _rating_pill(r["prev_action"], r["prev_grade"], muted=True)
    curr = _rating_pill(r["action"], r["grade"])
    arrow = '<span style="color:#94a3b8;font-size:15px;padding:0 8px;">&rarr;</span>'
    return prev + arrow + curr


# Colored open-P&L cell (green gain / red loss) with percent.
def _pnl_html(open_pnl, pnl_pct) -> str:
    if open_pnl is None:
        return '<span style="color:#94a3b8;">&mdash;</span>'
    color = "#059669" if open_pnl >= 0 else "#dc2626"
    pct = f"{pnl_pct * 100:+.1f}%" if pnl_pct is not None else "—"
    return (f'<span style="color:{color};font-weight:600;">{open_pnl:+,.0f}</span> '
            f'<span style="color:#94a3b8;">({_esc(pct)})</span>')


# One label-over-value stat cell for a card's stat strip.
def _stat_cell(label: str, value_html: str) -> str:
    return (
        '<td style="padding:0 16px 0 0;vertical-align:top;">'
        f'<div style="color:#94a3b8;font:600 10px/1.4 {FONT};text-transform:uppercase;'
        f'letter-spacing:.5px;">{_esc(label)}</div>'
        f'<div style="color:#0f172a;font:600 14px/1.4 {FONT};margin-top:3px;">{value_html}</div>'
        '</td>'
    )


# Nearest-catalyst cell with an overdue flag.
def _catalyst_html(r: dict) -> str:
    days = r["nearest_catalyst_days"]
    if days is None:
        return '<span style="color:#94a3b8;">&mdash;</span>'
    s = f"{days}d"
    if r["has_overdue_catalyst"]:
        s += ' <span style="color:#dc2626;">&#9888; overdue</span>'
    return s


# Driver evidence rendered as rounded chips (capped at 8).
def _drivers_html(drivers: list[str]) -> str:
    if not drivers:
        return ""
    chips = "".join(
        f'<span style="display:inline-block;background:#f1f5f9;border:1px solid #e2e8f0;'
        f'border-radius:999px;padding:3px 10px;margin:4px 4px 0 0;color:#475569;'
        f'font:500 12px/1.5 {FONT};">{_esc(d)}</span>'
        for d in drivers[:8]
    )
    return f'<div style="margin-top:10px;">{chips}</div>'


# The plain-English rationale block (newlines → <br>).
def _note_html(note: str) -> str:
    if not note:
        return (f'<div style="margin-top:12px;color:#94a3b8;font:italic 13px/1.5 {FONT};">'
                'No rationale computed yet — run a recompute.</div>')
    safe = _esc(note.strip()).replace("\n", "<br>")
    return f'<div style="margin-top:12px;color:#334155;font:14px/1.6 {FONT};">{safe}</div>'


# A full position card: header (ticker + rating), transition, stats, rationale,
# drivers, and a context footer. `accent` colors the left rule by change kind.
def _card(r: dict) -> str:
    accent = ACCENT.get(r["change_kind"], "#cbd5e1")
    company = (f'<span style="color:#64748b;font:400 13px/1.2 {FONT};">'
               f'&nbsp;&middot; {_esc(r["company_name"])}</span>') if r["company_name"] else ""
    header = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td style="vertical-align:middle;">'
        f'<span style="color:#0f172a;font:700 17px/1.2 {FONT};">{_esc(r["ticker"])}</span>{company}'
        '</td>'
        '<td align="right" style="vertical-align:middle;">'
        f'{_rating_pill(r["action"], r["grade"])}</td>'
        '</tr></table>'
    )
    trans = ""
    if r["change_kind"] in ("new_sell", "deterioration", "improvement"):
        trans = f'<div style="margin-top:12px;">{_transition(r)}</div>'
    risk = f'{r["risk_score"]:.0f}' if r["risk_score"] is not None else "&mdash;"
    stats = (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px;"><tr>'
        + _stat_cell("NAV", f'{r["pct_nav"] * 100:.1f}%')
        + _stat_cell("Open P&L", _pnl_html(r["open_pnl"], r["pnl_pct"]))
        + _stat_cell("Risk", risk)
        + _stat_cell("Catalyst", _catalyst_html(r))
        + '</tr></table>'
    )
    foot_bits = []
    if r["confidence"] is not None:
        foot_bits.append(f'confidence {r["confidence"] * 100:.0f}%')
    foot_bits.append(f'tier {r["conviction_tier"]}')
    if r["stage"]:
        foot_bits.append(_esc(str(r["stage"]).replace("_", " ")))
    foot = (f'<div style="margin-top:14px;color:#94a3b8;font:12px/1.4 {FONT};">'
            f'{" &middot; ".join(foot_bits)}</div>')
    inner = header + trans + stats + _note_html(r["note"]) + _drivers_html(r["drivers"]) + foot
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid {accent};'
        'border-radius:10px;margin:0 0 12px 0;">'
        f'<tr><td style="padding:16px 18px;">{inner}</td></tr></table>'
    )


# A titled section with a count badge and its cards. Empty → "".
def _section(title: str, subtitle: str, accent: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    badge = (f'<span style="display:inline-block;background:{accent};color:#fff;'
             f'font:700 12px/1 {FONT};padding:3px 8px;border-radius:999px;'
             f'margin-left:8px;vertical-align:middle;">{len(rows)}</span>')
    sub = (f'<div style="color:#64748b;font:400 12px/1.4 {FONT};margin-top:4px;">'
           f'{_esc(subtitle)}</div>') if subtitle else ""
    head = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:24px 0 12px 0;"><tr>'
        f'<td style="border-left:4px solid {accent};padding-left:12px;">'
        f'<div style="color:#0f172a;font:700 15px/1.2 {FONT};">{_esc(title)}{badge}</div>{sub}'
        '</td></tr></table>'
    )
    return head + "".join(_card(r) for r in rows)


# Executive stat band — four big-number tiles across the top of the body.
def _summary_band(buckets: dict[str, list[dict]], n_positions: int) -> str:
    tiles = [
        ("New SELL", len(buckets["downgrades_to_sell"]), "#dc2626"),
        ("Deteriorations", len(buckets["deteriorations"]), "#d97706"),
        ("Improvements", len(buckets["improvements"]), "#059669"),
        ("Monitored", n_positions, "#0f172a"),
    ]
    cells = ""
    for label, val, color in tiles:
        cells += (
            '<td width="25%" align="center" style="padding:6px;">'
            f'<div style="color:{color};font:700 26px/1 {FONT};">{val}</div>'
            f'<div style="color:#64748b;font:600 10px/1.3 {FONT};text-transform:uppercase;'
            f'letter-spacing:.5px;margin-top:6px;">{_esc(label)}</div></td>'
        )
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'<tr>{cells}</tr></table>')


# Reassuring green panel shown when there were zero rating/grade changes.
def _no_change_panel(n_positions: int) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:10px;'
        'margin:24px 0 0 0;"><tr><td style="padding:18px 20px;">'
        f'<div style="color:#047857;font:700 15px/1.3 {FONT};">'
        '&#10003; No rating or grade changes</div>'
        f'<div style="color:#065f46;font:400 13px/1.5 {FONT};margin-top:6px;">'
        f'All {n_positions} monitored positions hold their prior grade and action since the '
        'last session. Standing watch items remain under review.</div>'
        '</td></tr></table>'
    )


# Compact one-liners for positions seen for the first time (no prior baseline).
def _new_positions_html(rows: list[dict]) -> str:
    if not rows:
        return ""
    items = "".join(
        f'<li style="margin:3px 0;">{_esc(r["ticker"])} &mdash; '
        f'{_esc(_rating_label(r["action"], r["grade"]))}'
        + (f' &middot; {_esc(r["company_name"])}' if r["company_name"] else "")
        + '</li>'
        for r in rows
    )
    return (
        f'<div style="margin:24px 0 0;color:#475569;font:13px/1.6 {FONT};">'
        f'<div style="color:#0f172a;font:700 14px/1.2 {FONT};border-left:4px solid #94a3b8;'
        f'padding-left:12px;margin-bottom:8px;">Newly tracked ({len(rows)})</div>'
        f'<ul style="margin:0;padding-left:30px;">{items}</ul></div>'
    )


# Standing watch list — current C/D names that did NOT change today, as a tight
# table so the brief still conveys the full risk posture on a quiet day.
def _watch_table(rows: list[dict]) -> str:
    if not rows:
        return ""
    trs = ""
    for r in rows:
        trs += (
            '<tr>'
            f'<td style="padding:9px 10px;border-top:1px solid #e2e8f0;white-space:nowrap;">'
            f'{_rating_pill(r["action"], r["grade"])}</td>'
            f'<td style="padding:9px 10px;border-top:1px solid #e2e8f0;color:#0f172a;'
            f'font:600 13px {FONT};">{_esc(r["ticker"])}</td>'
            f'<td style="padding:9px 10px;border-top:1px solid #e2e8f0;color:#64748b;'
            f'font:400 12px {FONT};">{_esc(r["company_name"] or "")}</td>'
            f'<td align="right" style="padding:9px 10px;border-top:1px solid #e2e8f0;'
            f'color:#64748b;font:400 12px {FONT};white-space:nowrap;">'
            f'{r["pct_nav"] * 100:.1f}% NAV</td>'
            '</tr>'
        )
    head = (f'<div style="color:#0f172a;font:700 15px/1.2 {FONT};border-left:4px solid #94a3b8;'
            'padding-left:12px;margin:24px 0 10px;">'
            'Standing watch list &mdash; unchanged today</div>')
    return (head + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border:1px solid #e2e8f0;border-radius:10px;border-collapse:separate;'
            f'border-spacing:0;overflow:hidden;">{trs}</table>')


# Pretty long-form date, e.g. "Monday, June 1, 2026" (no platform-specific %-d).
def _weekday(date_iso: str) -> str:
    d = datetime.strptime(date_iso, "%Y-%m-%d")
    return d.strftime("%A, %B ") + str(d.day) + d.strftime(", %Y")


# Render the full HTML brief from pre-classified buckets. Pure (no DB) so it is
# directly unit-testable with synthetic rows.
def render_risk_brief_html(
    buckets: dict[str, list[dict]],
    rows: list[dict],
    *,
    date_iso: str,
    pulled_at: datetime | None = None,
) -> str:
    n = len(rows)
    has_changes = bool(
        buckets["downgrades_to_sell"] or buckets["deteriorations"] or buckets["improvements"]
    )
    body = _section(
        "Downgraded to SELL — action required",
        "Rating moved to SELL (grade D) from a prior HOLD. Close/trim review before next session.",
        "#dc2626", buckets["downgrades_to_sell"],
    )
    body += _section(
        "Grade deterioration — heightened watch",
        "Still HOLD, but the letter grade worsened versus the prior session.",
        "#d97706", buckets["deteriorations"],
    )
    body += _section(
        "Stabilizing & upgrades",
        "Grade improved or the rating recovered from SELL to HOLD.",
        "#059669", buckets["improvements"],
    )
    body += _new_positions_html(buckets["new_positions"])
    if not has_changes:
        body += _no_change_panel(n)
    body += _watch_table(buckets["watch_list"])

    preheader = _esc(_subject(date_iso, buckets))
    gen = datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M ET")
    pulled = f' &middot; Positions pulled {_esc(pulled_at.isoformat())}' if pulled_at else ""
    footer = (
        f'<div style="color:#94a3b8;font:11px/1.6 {FONT};">'
        'Methodology: each holding is scored across a 12-bucket risk taxonomy, '
        'red-teamed against a warning-sign catalog, and graded A&ndash;D '
        '(A clean &middot; B monitor &middot; C watch &middot; D broken &rarr; SELL). '
        'Changes are measured against the prior session&rsquo;s snapshot.<br>'
        f'Generated by SMA Monitor &middot; {gen}{pulled}<br>'
        'Confidential &mdash; for the portfolio manager. Not investment advice.'
        '</div>'
    )

    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"><title>AI CRO Daily Risk Brief</title></head>'
        '<body style="margin:0;padding:0;background:#eef2f6;">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#eef2f6;"><tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        'style="width:640px;max-width:640px;background:#ffffff;border-radius:14px;'
        'overflow:hidden;border:1px solid #e2e8f0;">'
        # masthead
        '<tr><td style="background:#0f172a;padding:26px 32px;">'
        f'<div style="color:#f8fafc;font:700 20px/1.2 {FONT};letter-spacing:.2px;">'
        'AI CRO &middot; Daily Risk &amp; Thesis-Drift Brief</div>'
        f'<div style="color:#94a3b8;font:400 13px/1.4 {FONT};margin-top:7px;">'
        f'{_esc(_weekday(date_iso))} &middot; red-team monitoring across {n} positions</div>'
        '</td></tr>'
        # summary band
        '<tr><td style="padding:22px 32px 4px;border-bottom:1px solid #e2e8f0;">'
        f'{_summary_band(buckets, n)}</td></tr>'
        # body
        f'<tr><td style="padding:8px 32px 28px;">{body}</td></tr>'
        # footer
        '<tr><td style="background:#f8fafc;padding:20px 32px;border-top:1px solid #e2e8f0;">'
        f'{footer}</td></tr>'
        '</table></td></tr></table></body></html>'
    )


# ---------------------------------------------------------------------------
# Plain-text rendering (Resend `text` part + stdout/archive fallback).
# ---------------------------------------------------------------------------

# Open-P&L as plain text, e.g. "+12,300 (+8.2%)".
def _pnl_text(r: dict) -> str:
    if r["open_pnl"] is None:
        return "—"
    pct = f"{r['pnl_pct'] * 100:+.1f}%" if r["pnl_pct"] is not None else "—"
    return f"{r['open_pnl']:+,.0f} ({pct})"


# One position rendered as an indented text block.
def _text_card(r: dict) -> str:
    out = []
    trans = ""
    if r["change_kind"] in ("new_sell", "deterioration", "improvement"):
        trans = (f"  [{_rating_label(r['prev_action'], r['prev_grade'])}"
                 f" -> {_rating_label(r['action'], r['grade'])}]")
    out.append(f"\n* {r['ticker']} — {_rating_label(r['action'], r['grade'])}{trans}")
    out.append(f"  {r['company_name'] or '—'} · tier {r['conviction_tier']} · "
               f"{str(r['stage']).replace('_', ' ')}")
    risk = f"{r['risk_score']:.0f}" if r["risk_score"] is not None else "—"
    cat = ""
    if r["nearest_catalyst_days"] is not None:
        cat = f" · catalyst {r['nearest_catalyst_days']}d"
    out.append(f"  {r['pct_nav'] * 100:.1f}% NAV · P&L {_pnl_text(r)} · risk {risk}{cat}")
    if r["note"]:
        for ln in r["note"].strip().splitlines():
            out.append(f"  {ln}")
    if r["drivers"]:
        out.append("  Drivers: " + "; ".join(r["drivers"][:8]))
    if r["confidence"] is not None:
        out.append(f"  confidence {r['confidence'] * 100:.0f}%")
    return "\n".join(out)


# Render the full plain-text brief from pre-classified buckets.
def render_risk_brief_text(
    buckets: dict[str, list[dict]],
    rows: list[dict],
    *,
    date_iso: str,
    pulled_at: datetime | None = None,
) -> str:
    n = len(rows)
    rule = "=" * 64
    lines = [
        "AI CRO — DAILY RISK & THESIS-DRIFT BRIEF",
        f"{_weekday(date_iso)} · {n} positions monitored",
        "",
        f"New SELL: {len(buckets['downgrades_to_sell'])}  |  "
        f"Deteriorations: {len(buckets['deteriorations'])}  |  "
        f"Improvements: {len(buckets['improvements'])}",
        rule,
    ]

    # Append a titled text block of cards when the bucket is non-empty.
    def block(title: str, bucket: list[dict]) -> None:
        if not bucket:
            return
        lines.append("")
        lines.append(title.upper())
        lines.append("-" * 64)
        for r in bucket:
            lines.append(_text_card(r))

    block("Downgraded to SELL — action required", buckets["downgrades_to_sell"])
    block("Grade deterioration — heightened watch", buckets["deteriorations"])
    block("Stabilizing & upgrades", buckets["improvements"])

    if not (buckets["downgrades_to_sell"] or buckets["deteriorations"] or buckets["improvements"]):
        lines.append("")
        lines.append(f"No rating or grade changes. All {n} positions hold their prior grades.")

    if buckets["new_positions"]:
        lines.append("")
        lines.append(f"NEWLY TRACKED ({len(buckets['new_positions'])})")
        lines.append("-" * 64)
        for r in buckets["new_positions"]:
            lines.append(f"  {r['ticker']} — {_rating_label(r['action'], r['grade'])}"
                         + (f" · {r['company_name']}" if r["company_name"] else ""))

    if buckets["watch_list"]:
        lines.append("")
        lines.append("STANDING WATCH LIST — UNCHANGED TODAY")
        lines.append("-" * 64)
        for r in buckets["watch_list"]:
            lines.append(f"  {_rating_label(r['action'], r['grade']):8} {r['ticker']:6} "
                         f"{(r['company_name'] or '')[:32]:32} {r['pct_nav'] * 100:5.1f}% NAV")

    lines.append("")
    lines.append(rule)
    lines.append("Generated by SMA Monitor · " + datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M ET"))
    lines.append("Confidential — for the portfolio manager. Not investment advice.")
    return "\n".join(lines) + "\n"


# Manual run: `python -m sma_monitor.outputs.risk_brief` assembles and dispatches
# the brief now (also echoing the text body to stdout) for local testing.
if __name__ == "__main__":
    result = assemble_risk_brief(prefer_stdout=True)
    print(json.dumps(result, indent=2, default=str))

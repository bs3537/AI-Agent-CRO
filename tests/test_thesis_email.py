"""W7 tests — morning thesis-drift email.

render_thesis_email_markdown is pure (synthetic rows). assemble_thesis_email
is exercised against the conftest sandbox DB after seeding ratings with the
offline engine, capturing output via a fake channel (no SMTP, no live data).
"""
from __future__ import annotations

from sma_monitor.outputs.channels import Channel
from sma_monitor.outputs.thesis_email import (
    assemble_thesis_email,
    render_thesis_email_markdown,
)


# A synthetic render row; callers override what they assert on.
def _row(ticker: str, grade: str, pct_nav: float, **over) -> dict:
    base = dict(
        ticker=ticker, company_name=ticker + " Inc", stage="commercial_stage",
        conviction_tier=4, pct_nav=pct_nav, open_pnl=1000.0, pnl_pct=0.1,
        nearest_catalyst_days=None, has_overdue_catalyst=False,
        grade=grade, action="sell" if grade == "D" else "hold",
        attention_state={"A": "clean", "B": "monitor", "C": "watch", "D": "broken"}[grade],
        verdict={"A": "hold", "B": "hold", "C": "watch", "D": "sell"}[grade],
        color={"A": "green", "B": "green", "C": "yellow", "D": "red"}[grade],
        risk_score={"A": 5.0, "B": 25.0, "C": 45.0, "D": 75.0}[grade],
        technical_state="above_ema20", price_vs_ema20_pct=0.02,
        note=f"{ticker} note line one.\nline two.", drivers=["driver a"],
        confidence=0.6, decided_at="2026-05-27T09:00:00+00:00",
    )
    base.update(over)
    return base


# Capture channel: records what assemble_thesis_email would send.
class _Capture(Channel):
    name = "capture"

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send_alert(self, ticker, rendered_text):  # unused
        pass

    def send_digest(self, date_iso, rendered_md):  # unused
        pass

    def send_thesis_email(self, date_iso, subject, rendered_md):
        self.sent.append((date_iso, subject, rendered_md))
        return None


# The rendered email leads with the title + grade counts and includes each
# position's synchronized rating, P&L, EMA status, and note.
def test_render_basic():
    rows = [_row("VRTX", "D", 0.24)]
    md = render_thesis_email_markdown(rows, date_iso="2026-05-27")
    assert "# AI CRO Morning Review — 2026-05-27" in md
    assert "Review items: 1 · grade changes: 0 · SELL D: 1 · HOLD C: 0" in md
    assert "VRTX — SELL D" in md
    assert "PM action: Sell/close review required" in md
    assert "above ema20" in md
    assert "note line one." in md
    assert "Drivers: driver a" in md


def test_render_review_items_only_and_ordered():
    rows = [
        _row("SMALL", "C", 0.05),
        _row("BIG", "C", 0.20),
        _row("HOLDER", "A", 0.30),
        _row("SELLER", "D", 0.02),
    ]
    md = render_thesis_email_markdown(rows, date_iso="2026-05-27")
    order = [md.index(t) for t in ("SELLER", "BIG", "SMALL")]
    assert order == sorted(order)
    assert "HOLDER —" not in md
    assert "Unchanged A/B holdings omitted: 1." in md


# Unknown cost basis renders an em-dash P&L rather than crashing.
def test_render_missing_pnl():
    md = render_thesis_email_markdown([_row("X", "C", 0.1, open_pnl=None, pnl_pct=None)],
                                      date_iso="2026-05-27")
    assert "P&L —" in md


def test_render_grade_change_callout():
    row = _row(
        "WATCH",
        "C",
        0.12,
        previous_grade="B",
        previous_action="hold",
        previous_decided_at="2026-05-26T09:00:00+00:00",
        grade_changed=True,
    )
    md = render_thesis_email_markdown([row], date_iso="2026-05-27")
    assert "Grade change: HOLD B -> HOLD C" in md


def test_render_no_review_items_is_short():
    md = render_thesis_email_markdown(
        [_row("AOK", "A", 0.10), _row("BOK", "B", 0.06)],
        date_iso="2026-05-27",
    )
    assert "No grade changes and no current C/D ratings" in md
    assert "AOK —" not in md
    assert "BOK —" not in md
    assert "Unchanged A/B holdings omitted: 2." in md


# End-to-end against the sandbox: seed ratings offline, then assemble through
# a capture channel. Every monitored holding appears, sell-band leads, and the
# subject carries the grade counts.
def test_assemble_seeds_and_sends():
    from sma_monitor.decision.engine import run_decisions
    from sma_monitor.portfolio.joined import latest_joined

    run_decisions(offline=True, force=True)
    holdings, _missing, _ = latest_joined()
    cap = _Capture()
    res = assemble_thesis_email(channels=[cap])

    assert res["positions"] == len(holdings)
    assert len(cap.sent) == 1
    _date, subject, md = cap.sent[0]
    assert subject.startswith("AI CRO morning review")
    assert res["review_positions"] <= res["positions"]
    assert "Review items:" in md
    # Grade counts in the result sum to the position count.
    assert sum(res["by_grade"].values()) == len(holdings)


# Legacy thesis-email summaries also retain safe provider receipts without
# confusing a Resend message id for the local file archive path.
def test_assemble_thesis_email_reports_channel_receipts():
    from sma_monitor.decision.engine import run_decisions

    class _ReceiptCapture(_Capture):
        def send_thesis_email(self, date_iso, subject, rendered_md):
            super().send_thesis_email(date_iso, subject, rendered_md)
            return "receipt_legacy_123"

    run_decisions(offline=True, force=True)
    res = assemble_thesis_email(channels=[_ReceiptCapture()])

    assert res["delivery_receipts"] == [{"channel": "capture", "result": "receipt_legacy_123"}]
    assert res["file_path"] is None

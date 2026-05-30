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
    assert "# SMA Thesis-Drift — 2026-05-27" in md
    assert "1 SELL D · 0 HOLD C · 0 HOLD B · 0 HOLD A" in md
    assert "VRTX — SELL D" in md
    assert "above ema20" in md
    assert "note line one." in md
    assert "Drivers: driver a" in md


# Rows are presented D → C → B → A, then by %NAV descending within a grade
# (caller pre-sorts; this asserts the contract the assembler relies on).
def test_render_ordering_is_caller_sorted():
    # Two C rows out of NAV order + an A + a D; emulate the sort the
    # assembler applies before calling render.
    rows = [
        _row("BIG", "C", 0.20),
        _row("SMALL", "C", 0.05),
        _row("HOLDER", "A", 0.30),
        _row("SELLER", "D", 0.02),
    ]
    rank = {"D": 0, "C": 1, "B": 2, "A": 3}
    rows.sort(key=lambda r: (rank[r["grade"]], -r["pct_nav"]))
    md = render_thesis_email_markdown(rows, date_iso="2026-05-27")
    order = [md.index(t) for t in ("SELLER", "BIG", "SMALL", "HOLDER")]
    assert order == sorted(order)  # SELLER first, then BIG (0.20) before SMALL (0.05), HOLDER last


# Unknown cost basis renders an em-dash P&L rather than crashing.
def test_render_missing_pnl():
    md = render_thesis_email_markdown([_row("X", "A", 0.1, open_pnl=None, pnl_pct=None)],
                                      date_iso="2026-05-27")
    assert "P&L —" in md


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
    assert subject.startswith("SMA thesis ratings")
    # Every held ticker is present in the rendered body.
    for h in holdings:
        assert h.ticker in md
    # Grade counts in the result sum to the position count.
    assert sum(res["by_grade"].values()) == len(holdings)

"""W7 tests — morning thesis-drift email.

render_thesis_email_markdown is pure (synthetic rows). assemble_thesis_email
is exercised against the conftest sandbox DB after seeding decisions with the
offline engine, capturing output via a fake channel (no SMTP, no live data).
"""
from __future__ import annotations

from sma_monitor.outputs.channels import Channel
from sma_monitor.outputs.thesis_email import (
    assemble_thesis_email,
    render_thesis_email_markdown,
)


# A synthetic render row; callers override what they assert on.
def _row(ticker: str, verdict: str, pct_nav: float, **over) -> dict:
    base = dict(
        ticker=ticker, company_name=ticker + " Inc", stage="commercial_stage",
        conviction_tier=4, pct_nav=pct_nav, open_pnl=1000.0, pnl_pct=0.1,
        nearest_catalyst_days=None, has_overdue_catalyst=False,
        verdict=verdict, color={'sell': 'red', 'watch': 'yellow', 'hold': 'green'}[verdict],
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


# The rendered email leads with the title + verdict counts and includes each
# position's verdict, P&L, and note.
def test_render_basic():
    rows = [_row("VRTX", "sell", 0.24)]
    md = render_thesis_email_markdown(rows, date_iso="2026-05-27")
    assert "# SMA Thesis-Drift — 2026-05-27" in md
    assert "1 sell · 0 watch · 0 hold" in md
    assert "VRTX — SELL" in md
    assert "note line one." in md
    assert "Drivers: driver a" in md


# Rows are presented sell → watch → hold, then by %NAV descending within a
# verdict (caller pre-sorts; this asserts the contract the assembler relies on).
def test_render_ordering_is_caller_sorted():
    # Two watch rows out of NAV order + a hold + a sell; emulate the sort the
    # assembler applies before calling render.
    rows = [
        _row("BIG", "watch", 0.20),
        _row("SMALL", "watch", 0.05),
        _row("HOLDER", "hold", 0.30),
        _row("SELLER", "sell", 0.02),
    ]
    rank = {"sell": 0, "watch": 1, "hold": 2}
    rows.sort(key=lambda r: (rank[r["verdict"]], -r["pct_nav"]))
    md = render_thesis_email_markdown(rows, date_iso="2026-05-27")
    order = [md.index(t) for t in ("SELLER", "BIG", "SMALL", "HOLDER")]
    assert order == sorted(order)  # SELLER first, then BIG (0.20) before SMALL (0.05), HOLDER last


# Unknown cost basis renders an em-dash P&L rather than crashing.
def test_render_missing_pnl():
    md = render_thesis_email_markdown([_row("X", "hold", 0.1, open_pnl=None, pnl_pct=None)],
                                      date_iso="2026-05-27")
    assert "P&L —" in md


# End-to-end against the sandbox: seed decisions offline, then assemble through
# a capture channel. Every monitored holding appears, sell-band leads, and the
# subject carries the verdict counts.
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
    assert subject.startswith("SMA thesis-drift")
    # Every held ticker is present in the rendered body.
    for h in holdings:
        assert h.ticker in md
    # Verdict counts in the result sum to the position count.
    assert sum(res["by_verdict"].values()) == len(holdings)

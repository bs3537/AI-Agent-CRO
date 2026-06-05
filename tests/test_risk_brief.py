"""W7 tests — daily risk brief (change classification, rendering, day-over-day).

Pure-function tests use synthetic brief rows. The snapshot + assemble tests run
against the conftest SQLite sandbox; assemble routes through a capture channel
so nothing leaves the process.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sma_monitor.outputs.channels import Channel
from sma_monitor.outputs.risk_brief import (
    _classify_kind,
    _subject,
    assemble_risk_brief,
    classify_changes,
    render_risk_brief_html,
    render_risk_brief_text,
)


# Build a synthetic brief row, deriving change_kind from the prior grade/action
# exactly as _brief_row would. Callers override only what they assert on.
def _brow(ticker, grade, pct_nav, *, action=None, prev_grade=None, prev_action=None,
          note="rationale line one.\nline two.", drivers=("driver a",), **over) -> dict:
    action = action or ("sell" if grade == "D" else "hold")
    has_prev = prev_grade is not None
    if prev_action is None and prev_grade is not None:
        prev_action = "sell" if prev_grade == "D" else "hold"
    attention = {"A": "clean", "B": "monitor", "C": "watch", "D": "broken"}.get(grade, "unknown")
    base = dict(
        ticker=ticker, company_name=f"{ticker} Inc", stage="commercial_stage",
        conviction_tier=4, pct_nav=pct_nav, open_pnl=1000.0, pnl_pct=0.1,
        nearest_catalyst_days=None, has_overdue_catalyst=False,
        grade=grade, action=action, attention_state=attention,
        risk_score={"A": 5.0, "B": 25.0, "C": 45.0, "D": 75.0}.get(grade),
        technical_state="above_ema20", price_vs_ema20_pct=0.02,
        note=note, drivers=list(drivers), confidence=0.6,
        decided_at="2026-06-02T09:00:00+00:00",
        prev_grade=prev_grade, prev_action=prev_action,
        prev_captured_at="2026-06-01T09:00:00+00:00" if has_prev else None,
        has_previous=has_prev,
        change_kind=_classify_kind(action, grade, prev_action, prev_grade, has_prev),
    )
    base.update(over)
    return base


# Capture channel: records the (date, subject, html, text) the brief would send.
class _Capture(Channel):
    name = "capture"

    def __init__(self):
        self.sent: list[tuple] = []

    def send_alert(self, ticker, rendered_text):  # unused
        pass

    def send_digest(self, date_iso, rendered_md):  # unused
        pass

    def send_risk_brief(self, date_iso, subject, html, text):
        self.sent.append((date_iso, subject, html, text))
        return None


# The classifier maps each day-over-day move to the right change kind.
def test_classify_kind():
    assert _classify_kind("hold", "C", "hold", "B", True) == "deterioration"
    assert _classify_kind("sell", "D", "hold", "C", True) == "new_sell"
    assert _classify_kind("hold", "B", "hold", "C", True) == "improvement"
    assert _classify_kind("hold", "A", "hold", "A", True) == "none"
    assert _classify_kind("hold", "C", None, None, False) == "new"
    assert _classify_kind("sell", "D", "sell", "D", True) == "none"
    assert _classify_kind("hold", "C", "sell", "D", True) == "improvement"


# Buckets: SELL flips lead by NAV, deteriorations lead worst-grade-first.
def test_classify_changes_orders():
    rows = [
        _brow("SMALLSELL", "D", 0.02, prev_grade="C"),  # new_sell, small NAV
        _brow("BIGSELL", "D", 0.20, prev_grade="B"),    # new_sell, big NAV
        _brow("DET_B", "B", 0.10, prev_grade="A"),      # deterioration A->B
        _brow("DET_C", "C", 0.05, prev_grade="B"),      # deterioration B->C (worse)
        _brow("IMP", "B", 0.08, prev_grade="C"),        # improvement
        _brow("STABLE", "A", 0.30, prev_grade="A"),     # unchanged
    ]
    b = classify_changes(rows)
    assert [r["ticker"] for r in b["downgrades_to_sell"]] == ["BIGSELL", "SMALLSELL"]
    assert [r["ticker"] for r in b["deteriorations"]] == ["DET_C", "DET_B"]
    assert [r["ticker"] for r in b["improvements"]] == ["IMP"]


# Subject leads with exception counts, or says so when there are none.
def test_subject():
    rows = [_brow("X", "D", 0.1, prev_grade="C"), _brow("Y", "C", 0.1, prev_grade="B")]
    s = _subject("2026-06-02", classify_changes(rows))
    assert "1 new SELL" in s and "1 grade deterioration" in s
    quiet = classify_changes([_brow("Z", "A", 0.1, prev_grade="A")])
    assert "No rating or grade changes" in _subject("2026-06-02", quiet)


# HTML renders the SELL section before the deterioration section and escapes
# model-supplied text (no raw angle brackets leak through).
def test_render_html_orders_and_escapes():
    rows = [
        _brow("AAA", "C", 0.05, prev_grade="B", note="thesis softening <urgent>"),
        _brow("BBB", "D", 0.10, prev_grade="C"),
    ]
    html = render_risk_brief_html(classify_changes(rows), rows, date_iso="2026-06-02")
    assert "Downgraded to SELL" in html
    assert "Grade deterioration" in html
    assert html.index("Downgraded to SELL") < html.index("Grade deterioration")
    assert "SELL D" in html and "HOLD C" in html
    assert "&lt;urgent&gt;" in html
    assert "<urgent>" not in html


# With no changes, the brief shows the reassurance panel and a standing watch
# list of unchanged C/D names.
def test_render_html_no_change_panel():
    rows = [_brow("AAA", "A", 0.2, prev_grade="A"), _brow("CCC", "C", 0.1, prev_grade="C")]
    html = render_risk_brief_html(classify_changes(rows), rows, date_iso="2026-06-02")
    assert "No rating or grade changes" in html
    assert "Standing watch list" in html
    assert "CCC" in html


# Plain-text fallback mirrors the sections and shows the transition.
def test_render_text():
    rows = [_brow("BBB", "D", 0.1, prev_grade="C")]
    t = render_risk_brief_text(classify_changes(rows), rows, date_iso="2026-06-02")
    assert "DOWNGRADED TO SELL" in t
    assert "BBB — SELL D" in t
    assert "HOLD C -> SELL D" in t


# previous_snapshots returns each ticker's most recent snapshot strictly before
# the given date (handling multi-day gaps).
def test_previous_snapshots_picks_latest_prior_day():
    from sma_monitor.decision.snapshots import previous_snapshots, upsert_snapshot

    upsert_snapshot(snapshot_date="2026-05-28", ticker="ZZZ", action="hold",
                    grade="A", attention_state="clean")
    upsert_snapshot(snapshot_date="2026-05-30", ticker="ZZZ", action="hold",
                    grade="B", attention_state="monitor")
    assert previous_snapshots("2026-05-31")["ZZZ"]["grade"] == "B"
    assert previous_snapshots("2026-05-29")["ZZZ"]["grade"] == "A"
    assert "ZZZ" not in previous_snapshots("2026-05-28")


# End-to-end against the sandbox: stamp current ratings + a prior snapshot, then
# assemble. A SELL flip and a HOLD grade deterioration are both detected and the
# capture channel receives an HTML + text brief.
def test_assemble_risk_brief_detects_changes():
    from sma_monitor.decision.schema import PositionRating
    from sma_monitor.decision.snapshots import upsert_snapshot
    from sma_monitor.decision.store import save_rating
    from sma_monitor.portfolio.joined import latest_joined
    from sma_monitor.portfolio.schema import Position
    from sma_monitor.portfolio.store import save_pull

    seed_at = datetime(2098, 1, 1, tzinfo=UTC)
    nav = 1_000_000.0
    save_pull(
        [
            Position(
                ticker="VRTX",
                qty=500,
                market_value=250_000.0,
                pct_nav=0.25,
                cost_basis=150_000.0,
                pulled_at=seed_at,
                nav=nav,
            ),
            Position(
                ticker="MRNA",
                qty=2_000,
                market_value=75_000.0,
                pct_nav=0.075,
                cost_basis=90_000.0,
                pulled_at=seed_at,
                nav=nav,
            ),
        ],
        nav=nav,
        pulled_at=seed_at,
        source="risk_brief_test_seed",
        raw_xml=None,
    )

    # Drive the scenario off this test-owned latest pull rather than whatever
    # earlier API/delete tests left in the shared sandbox.
    holdings, _missing, _ = latest_joined()
    tickers = [h.ticker for h in holdings]
    assert len(tickers) >= 2, f"need >=2 monitored holdings, got {tickers}"
    t_sell, t_det = tickers[0], tickers[1]

    def _mk(ticker, action, grade, attention):
        return PositionRating(
            ticker=ticker, action=action, grade=grade, attention_state=attention,
            risk_score=50.0, risk_components={}, technical_state="below_ema20",
            deterministic_grade=grade, llm_grade=None, final_grade=grade,
            note=f"{ticker} thesis under pressure.", drivers=["d1", "d2"], confidence=0.7,
            thesis_hash="th_" + ticker, inputs_hash="ih_" + ticker,
            model_used="heuristic-test", rating_version="vtest",
            # Far-future stamp so these win latest_ratings() over other tests'
            # 2035-dated rows in the shared session sandbox.
            decided_at=datetime(2099, 1, 1, tzinfo=UTC),
        )

    save_rating(_mk(t_sell, "sell", "D", "broken"))  # today: SELL D
    save_rating(_mk(t_det, "hold", "C", "watch"))    # today: HOLD C
    # Prior session: t_sell was HOLD C, t_det was HOLD B.
    upsert_snapshot(snapshot_date="2026-06-01", ticker=t_sell, action="hold",
                    grade="C", attention_state="watch")
    upsert_snapshot(snapshot_date="2026-06-01", ticker=t_det, action="hold",
                    grade="B", attention_state="monitor")

    cap = _Capture()
    res = assemble_risk_brief(date_iso="2026-06-02", channels=[cap])

    assert t_sell in res["downgrades_to_sell"]
    assert t_det in res["deteriorations"]
    assert len(cap.sent) == 1
    _date, subject, html, text = cap.sent[0]
    assert "new SELL" in subject
    assert t_sell in html and t_det in html
    assert "SELL D" in html
    assert t_sell in text

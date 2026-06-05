from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from sma_monitor.api.app import create_app
from sma_monitor.decision.engine import run_decisions
from sma_monitor.decision.store import latest_rating
from sma_monitor.portfolio.joined import latest_joined
from sma_monitor.portfolio.schema import Position, Sidecar
from sma_monitor.portfolio.sidecar import load_sidecar, set_thesis, write_sidecar
from sma_monitor.portfolio.store import save_pull


class FakeDraftProvider:
    model_label = "fake-codex-draft"

    def __init__(self, thesis: str = "AI draft thesis for NEWD."):
        self.thesis = thesis
        self.calls: list[dict] = []

    def complete_json(self, *, system, user, schema=None, max_tokens=512) -> dict:
        self.calls.append({"system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        return {
            "company_name": "NewDraft Bio",
            "stage": "clinical_stage",
            "conviction_tier": 3,
            "thesis": self.thesis,
            "initial_grade": "B",
            "grade_rationale": "New position has a plausible but unreviewed AI thesis; monitor until PM review.",
            "drivers": ["AI-generated draft", "PM review required"],
            "confidence": 0.61,
        }

    def complete_text(self, *, system, user, max_tokens=600) -> str:
        return self.thesis


class FakeDecisionProvider:
    model_label = "fake-codex-decision"

    def complete_json(self, *, system, user, schema=None, max_tokens=512) -> dict:
        return {
            "llm_grade": "B",
            "thesis_clause_impacts": [],
            "hard_breaker": {"present": False, "type": "none", "evidence": "none"},
            "technical_assessment": {
                "uses_ema20": False,
                "interpretation": "No price data in unit test.",
                "should_affect_grade": "none",
            },
            "note": "Initial Codex decision against the AI draft thesis.",
            "drivers": ["draft thesis assessed"],
            "confidence": 0.72,
        }

    def complete_text(self, *, system, user, max_tokens=600) -> str:
        return "fake narrative"


@pytest.fixture(scope="module", autouse=True)
def _restore_seed_positions_after_module():
    from sma_monitor.portfolio.store import latest_positions as _latest_positions

    original_positions, _ = _latest_positions()
    yield
    if original_positions:
        restored_at = datetime(2300, 1, 1, tzinfo=UTC)
        restored = [p.model_copy(update={"pulled_at": restored_at}) for p in original_positions]
        save_pull(
            restored,
            nav=restored[0].nav,
            pulled_at=restored_at,
            source="test_new_position_restore",
            raw_xml=None,
        )


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def _save_positions(*tickers: str) -> list[Position]:
    pulled_at = datetime(2099, 1, 1, tzinfo=UTC)
    nav = 1_000_000.0
    positions = [
        Position(
            ticker=t,
            qty=100,
            market_value=25_000.0,
            pct_nav=0.025,
            cost_basis=20_000.0,
            pulled_at=pulled_at,
            nav=nav,
        )
        for t in tickers
    ]
    save_pull(positions, nav=nav, pulled_at=pulled_at, source="test_new_position", raw_xml=None)
    return positions


def test_bootstrap_creates_ai_draft_sidecar_and_initial_rating_for_missing_position():
    from sma_monitor.portfolio.draft_thesis import bootstrap_ai_draft_sidecars

    positions = _save_positions("NEWD")
    provider = FakeDraftProvider(thesis="AI-generated draft: NEWD is a newly detected position with clinical upside.")

    state = bootstrap_ai_draft_sidecars(
        positions=positions,
        provider=provider,
        compute_source="test_new_position_draft",
    )

    assert state["created"] == 1
    assert state["created_tickers"] == ["NEWD"]
    assert state["ratings_created"] == 1
    assert provider.calls, "Codex/draft provider should be asked to create the draft"

    sc = load_sidecar("NEWD")
    assert sc is not None
    assert sc.thesis.startswith("AI-generated draft")
    assert sc.thesis_source == "ai_generated"
    assert sc.thesis_status == "draft"
    assert sc.thesis_generated_by == "fake-codex-draft"
    assert sc.thesis_compute_source == "test_new_position_draft"
    assert sc.draft_rating_grade == "B"

    rating = latest_rating("NEWD")
    assert rating is not None
    assert rating["grade"] == "B"
    assert rating["model_used"] == "fake-codex-draft"
    assert rating["compute_source"] == "test_new_position_draft"

    holdings, missing, _ = latest_joined()
    assert "NEWD" in {h.ticker for h in holdings}
    assert "NEWD" not in missing


def test_bootstrap_does_not_overwrite_pm_thesis_and_pm_edit_clears_draft_marker():
    from sma_monitor.portfolio.draft_thesis import bootstrap_ai_draft_sidecars

    positions = _save_positions("PMOK")
    write_sidecar(
        Sidecar(
            ticker="PMOK",
            conviction_tier=4,
            stage="commercial_stage",
            thesis="PM-authored durable franchise thesis.",
            company_name="PM OK Corp",
        )
    )

    state = bootstrap_ai_draft_sidecars(
        positions=positions,
        provider=FakeDraftProvider(thesis="This should not overwrite PM text."),
        compute_source="test_new_position_draft",
    )

    assert state["created"] == 0
    assert state["skipped_existing_pm"] == ["PMOK"]
    assert load_sidecar("PMOK").thesis == "PM-authored durable franchise thesis."

    # If a PM edits a draft, the sidecar becomes PM-authored/active and future
    # bootstraps must leave it alone.
    draft_positions = _save_positions("PMRV")
    bootstrap_ai_draft_sidecars(
        positions=draft_positions,
        provider=FakeDraftProvider(thesis="AI draft before PM review."),
        compute_source="test_new_position_draft",
    )
    set_thesis("PMRV", "PM-reviewed thesis replaces the AI draft.")
    reviewed = load_sidecar("PMRV")
    assert reviewed.thesis == "PM-reviewed thesis replaces the AI draft."
    assert reviewed.thesis_source == "pm"
    assert reviewed.thesis_status == "active"

    state2 = bootstrap_ai_draft_sidecars(
        positions=draft_positions,
        provider=FakeDraftProvider(thesis="Second AI draft should not overwrite."),
        compute_source="test_new_position_draft",
    )
    assert state2["created"] == 0
    assert state2["skipped_existing_pm"] == ["PMRV"]
    assert load_sidecar("PMRV").thesis == "PM-reviewed thesis replaces the AI draft."


def test_dashboard_api_surfaces_ai_draft_metadata(client):
    from sma_monitor.portfolio.draft_thesis import bootstrap_ai_draft_sidecars

    positions = _save_positions("AIDF")
    bootstrap_ai_draft_sidecars(
        positions=positions,
        provider=FakeDraftProvider(thesis="AI draft visible in dashboard metadata."),
        compute_source="test_new_position_draft",
    )

    r = client.get("/api/positions")
    assert r.status_code == 200
    row = next(p for p in r.json()["positions"] if p["ticker"] == "AIDF")
    assert row["thesis_source"] == "ai_generated"
    assert row["thesis_status"] == "draft"
    assert row["is_ai_generated_thesis"] is True
    assert row["thesis_generated_by"] == "fake-codex-draft"
    assert row["rating"]["grade"] == "B"


def test_smart_morning_recompute_bootstraps_missing_sidecars_before_join(monkeypatch):
    from sma_monitor.orchestrator import smart_recompute
    from sma_monitor.portfolio import draft_thesis

    _save_positions("SMRT")
    monkeypatch.setattr(
        draft_thesis,
        "get_provider",
        lambda **kwargs: FakeDraftProvider(thesis="AI draft created before morning join."),
    )
    monkeypatch.setattr(
        smart_recompute,
        "_refresh_all_daily_signals",
        lambda tickers: {
            "company_news": {"by_ticker": {ticker: {"new": 0} for ticker in tickers}},
            "sec": {"by_ticker": {ticker: {"articles_new": 0} for ticker in tickers}},
            "financials": {"updated": len(tickers)},
            "prices": {"updated": len(tickers)},
        },
    )
    monkeypatch.setattr(smart_recompute, "latest_price_series", lambda ticker: [100.0] * 26)

    state = smart_recompute.run_smart_morning_recompute(
        offline=False,
        refresh_positions_fn=lambda **kwargs: {"refreshed": True, "reason": "test"},
    )

    assert state["new_position_drafts"]["created_tickers"] == ["SMRT"]
    assert "SMRT" in {row["ticker"] for row in state["quiet"] + state["triggers"]}
    assert "SMRT" not in state["missing_sidecars"]
    assert load_sidecar("SMRT").thesis_source == "ai_generated"


def test_full_decision_engine_can_recompute_against_bootstrapped_ai_draft(monkeypatch):
    from sma_monitor.portfolio.draft_thesis import bootstrap_ai_draft_sidecars

    positions = _save_positions("RNEW")
    bootstrap_ai_draft_sidecars(
        positions=positions,
        provider=FakeDraftProvider(thesis="AI draft thesis that the decision engine can assess."),
        compute_source="test_new_position_draft",
    )
    monkeypatch.setattr(
        "sma_monitor.decision.engine.get_provider",
        lambda **kwargs: FakeDecisionProvider(),
    )
    monkeypatch.setattr(
        "sma_monitor.orchestrator.cost.record_llm_call",
        lambda **kwargs: None,
    )

    state = run_decisions(
        offline=False,
        only_ticker="RNEW",
        force=True,
        compute_source="test_new_position_initial_decision",
    )

    assert state["decided"] == 1
    rating = latest_rating("RNEW")
    assert rating["grade"] == "B"
    assert rating["model_used"] == "fake-codex-decision"
    assert rating["compute_source"] == "test_new_position_initial_decision"

"""Run one full orchestration cycle.

Order (PLAN.MD §6 — sequence vs. parallel):
  1. Refresh positions if stale (Flex pull). On failure set 'stale_positions'.
  2. News poll (with degrade-aware bucket skipping).
  3. Score unscored pairs (heuristic when offline / cost-degraded).
  4. Red team on composite ≥ T₂ (or only ≥ T if step-1 of cascade is active).
  5. Dispatch alerts for composite ≥ T (with the Phase 5 suppression rules).
  6. Optionally assemble digest (run by the schedule loop at digest_time).

Every stage is idempotent at the persistence layer — re-running this
function is safe and only acts on rows that lack a stage's output.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..news.pipeline import poll as news_poll
from ..outputs.alerts import run_alerts
from ..outputs.digest import assemble_digest
from ..portfolio.flex import FlexError, fetch_statement, parse_positions
from ..portfolio.store import latest_positions, save_pull
from ..red_team.pipeline import run_red_team
from ..scorer.multipliers import T
from ..scorer.pipeline import score_unscored
from .cost import DegradeState, current_degrade_state
from .flags import clear_flag, set_flag

log = logging.getLogger("sma_monitor.orchestrator.pipeline")

POSITION_STALE_HOURS = 12  # refresh if last pull is older than this


def maybe_refresh_positions(*, force: bool = False, now: datetime | None = None) -> dict:
    """Return {'refreshed': bool, 'reason': str, 'pulled_at': iso|None}."""
    now = now or datetime.now(timezone.utc)
    _, last_pulled_at = latest_positions()
    if last_pulled_at is not None and not force:
        age = now - last_pulled_at
        if age < timedelta(hours=POSITION_STALE_HOURS):
            return {"refreshed": False, "reason": "fresh",
                    "pulled_at": last_pulled_at.isoformat(),
                    "age_hours": round(age.total_seconds() / 3600, 2)}

    missing = settings.missing_for(1)
    if missing:
        set_flag("stale_positions",
                 metadata={"reason": "ibkr_flex_creds_missing",
                           "missing": missing,
                           "last_pulled_at": last_pulled_at.isoformat()
                           if last_pulled_at else None})
        return {"refreshed": False, "reason": "creds_missing",
                "pulled_at": last_pulled_at.isoformat() if last_pulled_at else None}

    assert settings.ibkr_flex_token and settings.ibkr_flex_query_id
    try:
        raw = fetch_statement(
            token=settings.ibkr_flex_token,
            query_id=settings.ibkr_flex_query_id,
        )
        positions, nav = parse_positions(raw.xml, pulled_at=raw.pulled_at)
        save_pull(positions, nav=nav, pulled_at=raw.pulled_at,
                  source="ibkr_flex", raw_xml=raw.xml)
        clear_flag("stale_positions")
        return {"refreshed": True, "reason": "ok",
                "pulled_at": raw.pulled_at.isoformat()}
    except (FlexError, Exception) as e:
        set_flag("stale_positions",
                 metadata={"reason": "flex_pull_failed", "err": str(e)[:200],
                           "last_pulled_at": last_pulled_at.isoformat()
                           if last_pulled_at else None})
        log.error("flex_refresh_failed", extra={"err": str(e)})
        return {"refreshed": False, "reason": "flex_failed",
                "pulled_at": last_pulled_at.isoformat() if last_pulled_at else None}


def run_one_cycle(
    *,
    offline: bool = False,
    refresh_positions: bool = True,
    include_news: bool = True,
    include_scoring: bool = True,
    include_red_team: bool = True,
    include_alerts: bool = True,
    include_digest: bool = False,
    news_lookback_hours: int = 24,
    news_num_results: int = 5,
    news_fixture: str | None = None,
) -> dict:
    """Execute one ordered pass through the agent."""
    state: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
    degrade = current_degrade_state()
    state["degrade"] = _degrade_dict(degrade)

    # Flag for budget pressure — visible to the digest.
    if degrade.fraction_spent >= 0.60:
        set_flag("budget_degraded", metadata={
            "spent_usd": degrade.spent_usd, "budget_usd": degrade.budget_usd,
            "fraction": round(degrade.fraction_spent, 3),
        })
    else:
        clear_flag("budget_degraded")

    if refresh_positions:
        state["positions"] = maybe_refresh_positions()

    if include_news:
        from pathlib import Path
        try:
            state["news"] = news_poll(
                api_key=settings.exa_api_key,
                from_file=Path(news_fixture) if news_fixture else None,
                num_results=news_num_results,
                lookback_hours=news_lookback_hours,
                skip_bucket_ids=degrade.skipped_bucket_ids or None,
            )
            clear_flag("exa_failure")
        except RuntimeError as e:
            # PLAN §6: "Exa down → skip ingestion cycle, don't crash; flag in next digest."
            log.warning("news_poll_skipped", extra={"err": str(e)})
            set_flag("exa_failure", metadata={"reason": "no_source",
                                              "detail": str(e)[:200]})
            state["news"] = {"status": "skipped", "reason": str(e)[:200]}

    if include_scoring:
        state["scoring"] = score_unscored(
            api_key=settings.anthropic_api_key,
            offline=offline,
        )

    if include_red_team:
        min_override = T if degrade.skip_red_team_t2_t_band else None
        state["red_team"] = run_red_team(
            api_key=settings.anthropic_api_key,
            offline=offline,
            min_composite_override=min_override,
        )

    if include_alerts:
        state["alerts"] = run_alerts()

    if include_digest:
        state["digest"] = assemble_digest(
            with_narrative=False,  # narrative is a separate Opus call; off by default in tick
            api_key=settings.anthropic_api_key,
        )

    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    log.info("cycle_done", extra={"summary": state})
    return state


def _degrade_dict(d: DegradeState) -> dict:
    return {
        "spent_usd": d.spent_usd,
        "budget_usd": d.budget_usd,
        "fraction": round(d.fraction_spent, 3),
        "skip_red_team_t2_t_band": d.skip_red_team_t2_t_band,
        "reduce_poll_frequency": d.reduce_poll_frequency,
        "drop_buckets_10_11": d.drop_buckets_10_11,
        "drop_bucket_12": d.drop_bucket_12,
    }

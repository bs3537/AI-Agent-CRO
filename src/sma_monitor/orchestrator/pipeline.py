"""Run orchestration cycles.

Daily-batch operating model (post-tuning):
  - 6 PM ET  → run_collect_cycle:
      positions + news + literature + SEC/FMP financials + score + red-team + decisions
  - 9 PM ET  → run_dispatch_cycle:  digest assembly + email delivery
  - Real-time alerts disabled — everything above T rolls into the digest.

Legacy `run_one_cycle` is preserved for ad-hoc CLI testing (`tick` command).

Every stage is idempotent at the persistence layer — re-running any of these
is safe and only acts on rows that lack a stage's output.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import settings
from ..decision.engine import run_decisions
from ..news.fmp_client import refresh_for_holdings, refresh_prices_for_holdings
from ..news.pipeline import poll as news_poll
from ..news.pipeline import poll_literature as news_poll_literature
from ..news.pipeline import poll_sec as news_poll_sec
from ..outputs.alerts import run_alerts
from ..outputs.digest import assemble_digest
from ..outputs.thesis_email import assemble_thesis_email
from ..portfolio.flex import FlexError, fetch_statement, parse_positions
from ..portfolio.store import latest_pull_at, save_pull
from ..red_team.pipeline import run_red_team
from ..scorer.multipliers import T
from ..scorer.pipeline import score_unscored
from .cost import DegradeState, current_degrade_state
from .flags import clear_flag, set_flag

log = logging.getLogger("sma_monitor.orchestrator.pipeline")

# Eastern timezone used to compute the digest's ET-local date so the
# "today's events" filter matches even when dispatch crosses UTC midnight.
ET = ZoneInfo("America/New_York")

# Refresh threshold for the positions snapshot. Anything older than this
# triggers a Flex pull at the start of a collect cycle.
POSITION_STALE_HOURS = 12


# Refresh the positions snapshot if it's older than POSITION_STALE_HOURS
# (or `force`). Sets the stale_positions flag on failure rather than
# crashing — downstream phases use the last known positions.
def maybe_refresh_positions(*, force: bool = False, now: datetime | None = None) -> dict:
    """Return {'refreshed': bool, 'reason': str, 'pulled_at': iso|None}."""
    now = now or datetime.now(UTC)
    last_pulled_at = latest_pull_at()
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


# Daily 6 PM ET collection — gather and process the day's data so the
# 9 PM dispatch has everything it needs. No alerts (disabled in batch
# mode), no digest (that's the dispatch step's job). Recomputes thesis-drift
# decisions last so the next morning's email reflects today's evidence.
def run_collect_cycle(*, offline: bool = False) -> dict:
    """Daily 6 PM ET collection step. Returns a state dict for logging."""
    return run_one_cycle(
        offline=offline,
        refresh_positions=True,
        include_news=True,
        include_literature=True,
        include_financials=True,
        include_scoring=True,
        include_red_team=True,
        include_decisions=True,
        include_alerts=False,
        include_digest=False,
    )


# Morning 9 AM ET thesis cycle — recompute any decisions whose thesis or
# evidence changed since they were last computed (force=False skips unchanged
# holdings), then assemble + send the thesis-drift email. Runs alongside the
# evening digest, not in place of it.
def run_morning_thesis_cycle(*, offline: bool = False) -> dict:
    """Daily 9 AM ET step: recompute stale decisions, then send the email."""
    state: dict = {"started_at": datetime.now(UTC).isoformat()}
    state["decisions"] = run_decisions(offline=offline)
    state["email"] = assemble_thesis_email()
    state["finished_at"] = datetime.now(UTC).isoformat()
    log.info("morning_thesis_done", extra={"summary": state})
    return state


# Daily 9 PM ET dispatch — assemble the digest with the Opus narrative and
# send via every configured channel (email + file). Uses the ET-local
# today so the events filter matches the collect cycle's ET date even
# when dispatch crosses UTC midnight. When the budget cascade has tripped
# step 2 (75%), the Opus call is skipped and the template fallback is used.
def run_dispatch_cycle(*, offline: bool = False) -> dict:
    """Daily 9 PM ET dispatch step. Returns a state dict for logging."""
    state: dict = {"started_at": datetime.now(UTC).isoformat()}
    degrade = current_degrade_state()
    state["degrade"] = _degrade_dict(degrade)
    et_today = datetime.now(tz=ET).date().isoformat()
    state["digest"] = assemble_digest(
        date_iso=et_today,
        with_narrative=True,  # PLAN §3: synthesis paragraph on the evening digest
        skip_opus=degrade.skip_opus_narrative,  # cascade step 2: template fallback
        api_key=settings.anthropic_api_key,
    )
    state["finished_at"] = datetime.now(UTC).isoformat()
    log.info("dispatch_done", extra={"summary": state})
    return state


# Legacy "all in one" cycle. Kept for ad-hoc testing via `tick` so the user
# can run every stage end-to-end without waiting for the schedule. Flags
# let the caller skip any stage individually.
def run_one_cycle(
    *,
    offline: bool = False,
    refresh_positions: bool = True,
    include_news: bool = True,
    include_literature: bool = False,
    include_financials: bool = False,
    include_scoring: bool = True,
    include_red_team: bool = True,
    include_decisions: bool = False,
    include_alerts: bool = True,
    include_digest: bool = False,
    digest_with_narrative: bool = False,
    news_lookback_hours: int = 24,
    news_num_results: int = 5,
    news_fixture: str | None = None,
) -> dict:
    """Execute one ordered pass through the agent."""
    state: dict = {"started_at": datetime.now(UTC).isoformat()}
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

    # W2: Semantic Scholar literature → bucket #10. Skipped (flagged) when no
    # key/fixture, exactly like the news poll — never crashes the cycle.
    if include_literature:
        try:
            state["literature"] = news_poll_literature(api_key=settings.semantic_scholar_api_key)
            clear_flag("literature_failure")
        except RuntimeError as e:
            log.warning("literature_poll_skipped", extra={"err": str(e)})
            set_flag("literature_failure", metadata={"reason": "no_source", "detail": str(e)[:200]})
            state["literature"] = {"status": "skipped", "reason": str(e)[:200]}

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

    # Financials in source-policy precedence: SEC filings (primary, bucket #7)
    # first, then FMP snapshots (secondary) for metrics + dashboard. Each is
    # flagged-and-skipped on failure, never crashing the cycle.
    if include_financials:
        try:
            state["sec"] = news_poll_sec(user_agent=settings.sec_edgar_user_agent)
            clear_flag("sec_failure")
        except Exception as e:
            log.warning("sec_poll_skipped", extra={"err": str(e)})
            set_flag("sec_failure", metadata={"reason": "error", "detail": str(e)[:200]})
            state["sec"] = {"status": "skipped", "reason": str(e)[:200]}
        try:
            state["financials"] = refresh_for_holdings(api_key=settings.fmp_api_key)
            # Daily price series for the dashboard sparklines (same key).
            state["prices"] = refresh_prices_for_holdings(api_key=settings.fmp_api_key)
            clear_flag("fmp_failure")
        except RuntimeError as e:
            # Covers both the no-key guard and a total fetch wipeout (FmpError is
            # a RuntimeError); detail carries which one so the flag is accurate.
            log.warning("financials_refresh_skipped", extra={"err": str(e)})
            set_flag("fmp_failure", metadata={"reason": "error", "detail": str(e)[:200]})
            state["financials"] = {"status": "skipped", "reason": str(e)[:200]}

    # Thesis-drift decisions roll up the freshly-scored evidence per position;
    # run after scoring + red team (+ financials) so the verdicts reflect today's data.
    if include_decisions:
        state["decisions"] = run_decisions(offline=offline)

    if include_alerts:
        state["alerts"] = run_alerts()

    if include_digest:
        state["digest"] = assemble_digest(
            with_narrative=digest_with_narrative,
            api_key=settings.anthropic_api_key,
        )

    state["finished_at"] = datetime.now(UTC).isoformat()
    log.info("cycle_done", extra={"summary": state})
    return state


# Render a DegradeState as a plain dict for inclusion in the logged cycle
# state. Frozen dataclasses don't serialize cleanly with default=str.
def _degrade_dict(d: DegradeState) -> dict:
    return {
        "spent_usd": d.spent_usd,
        "budget_usd": d.budget_usd,
        "fraction": round(d.fraction_spent, 3),
        "skip_red_team_t2_t_band": d.skip_red_team_t2_t_band,
        "skip_opus_narrative": d.skip_opus_narrative,
        "drop_buckets_10_11": d.drop_buckets_10_11,
        "drop_bucket_12": d.drop_bucket_12,
    }

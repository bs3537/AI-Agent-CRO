"""Run the red team over scores with composite ≥ T₂.

Per PLAN.MD §4:
  - Articles above T  → scorer + red team in the real-time pipeline
  - Articles in (T₂,T]→ red team only, for the digest

This module is the orchestration both bands route through. Phase 5 alert
formatter decides what to do with the band; this module just produces the
red-team pass row.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from ..news.buckets import load_buckets
from ..scorer.multipliers import T2
from .catalog import Catalog, load_catalog
from .claude_client import DEFAULT_MODEL, RedTeamClaudeError, red_team_with_claude
from .heuristic import MODEL_LABEL as HEURISTIC_MODEL, red_team_heuristically
from .schema import RedTeamCandidate, RedTeamResult
from .store import init_red_team_schema, pick_candidates, save_red_team_pass

log = logging.getLogger("sma_monitor.red_team.pipeline")

RedTeamFn = Callable[[RedTeamCandidate, Catalog], tuple[RedTeamResult, str]]


def run_red_team(
    *,
    api_key: str | None,
    offline: bool = False,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
) -> dict:
    init_red_team_schema()
    catalog = load_catalog()
    buckets = load_buckets()
    holdings, _missing, _ = latest_joined()
    holdings_by_ticker: dict[str, Holding] = {h.ticker: h for h in holdings}
    if not holdings_by_ticker:
        log.warning("no_holdings_loaded")
        return {"ran": 0, "errors": 0, "skipped": 0}

    if offline or not api_key:
        runner: RedTeamFn = lambda c, cat: (red_team_heuristically(c, cat), HEURISTIC_MODEL)
        runner_label = HEURISTIC_MODEL
    else:
        runner = lambda c, cat: red_team_with_claude(c, cat, api_key=api_key, model=model)
        runner_label = model

    rows = pick_candidates(T2, catalog.catalog_version, limit=limit)
    ran = errors = skipped = 0
    for row in rows:
        ticker = row["ticker"]
        holding = holdings_by_ticker.get(ticker)
        if holding is None:
            skipped += 1
            continue
        bucket = buckets.get(row["primary_bucket_id"])
        if bucket is None:
            skipped += 1
            continue
        candidate = RedTeamCandidate(
            score_event_id=row["score_event_id"],
            article_event_id=row["article_event_id"],
            title=row["title"] or "",
            excerpt=row["excerpt"] or "",
            source=row["source"],
            source_tier=row["source_tier"],
            published_at=_parse_dt(row["published_at"]),
            ticker=ticker,
            company_name=holding.company_name,
            pct_nav=holding.pct_nav,
            conviction_tier=int(holding.conviction_tier),
            stage=holding.stage,
            thesis=holding.thesis,
            nearest_catalyst_days=holding.nearest_catalyst_days,
            primary_bucket_id=row["primary_bucket_id"],
            primary_bucket_name=bucket.name,
            composite=row["composite"],
            threshold_band=row["threshold_band"],
            scorer_axes=(
                row["financial_impact"],
                row["narrative_shift"],
                row["time_criticality"],
            ),
            scorer_rationale=row["scorer_rationale"] or "",
            scorer_confidence=row["scorer_confidence"] or 0.0,
        )
        try:
            result, model_used = runner(candidate, catalog)
        except (RedTeamClaudeError, ValueError) as e:
            log.error("red_team_failed",
                      extra={"score_event_id": candidate.score_event_id,
                             "ticker": ticker, "err": str(e)})
            errors += 1
            continue

        save_red_team_pass(
            score_event_id=candidate.score_event_id,
            article_event_id=candidate.article_event_id,
            ticker=ticker,
            result=result,
            model_used=model_used,
            catalog_version=catalog.catalog_version,
            ran_at=datetime.now(timezone.utc),
        )
        ran += 1
        log.info(
            "red_team_done",
            extra={
                "ticker": ticker,
                "composite": candidate.composite,
                "band": candidate.threshold_band,
                "severity": result.severity_of_concern,
                "patterns": [m.id for m in result.matched_warning_signs],
                "title": candidate.title[:80],
            },
        )

    log.info("red_team_summary",
             extra={"ran": ran, "errors": errors, "skipped": skipped,
                    "model": runner_label, "catalog": catalog.catalog_version})
    return {"ran": ran, "errors": errors, "skipped": skipped}


def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

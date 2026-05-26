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

from ..llm import get_provider
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from ..news.buckets import load_buckets
from ..scorer.multipliers import T2
from .catalog import Catalog, load_catalog
from .claude_client import DEFAULT_MODEL, RedTeamClaudeError, red_team_with_llm
from .heuristic import MODEL_LABEL as HEURISTIC_MODEL, red_team_heuristically
from .schema import RedTeamCandidate, RedTeamResult
from .store import init_red_team_schema, pick_candidates, save_red_team_pass

log = logging.getLogger("sma_monitor.red_team.pipeline")

# Signature shared by Claude + heuristic red-team runners so the pipeline
# can swap them based on --offline / cost-degrade state.
RedTeamFn = Callable[[RedTeamCandidate, Catalog], tuple[RedTeamResult, str]]


# Run the red team across every above-T₂ score lacking a pass at the
# current catalog_version. min_composite_override raises the floor — Phase 6
# passes T here when budget pressure is on so only alert-band scores run.
def run_red_team(
    *,
    api_key: str | None,
    offline: bool = False,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
    min_composite_override: float | None = None,
) -> dict:
    """min_composite_override raises the effective floor from T₂. Phase 6
    degrade cascade passes T here when budget pressure is on, so only the
    real-time-alert band (composite ≥ T) gets red-teamed."""
    init_red_team_schema()
    catalog = load_catalog()
    buckets = load_buckets()
    holdings, _missing, _ = latest_joined()
    holdings_by_ticker: dict[str, Holding] = {h.ticker: h for h in holdings}
    if not holdings_by_ticker:
        log.warning("no_holdings_loaded")
        return {"ran": 0, "errors": 0, "skipped": 0}

    # Use the LLM provider when available (Codex login); otherwise heuristic.
    # `--offline` forces heuristic; `api_key` no longer gates.
    provider = get_provider(prefer_offline=offline)
    if provider is None:
        runner: RedTeamFn = lambda c, cat: (red_team_heuristically(c, cat), HEURISTIC_MODEL)
        runner_label = HEURISTIC_MODEL
    else:
        runner = lambda c, cat: red_team_with_llm(c, cat, provider=provider)
        runner_label = provider.model_label

    floor = T2 if min_composite_override is None else max(T2, min_composite_override)
    rows = pick_candidates(floor, catalog.catalog_version, limit=limit)
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


# Lenient ISO-8601 parser; returns None for missing/unparseable input.
def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

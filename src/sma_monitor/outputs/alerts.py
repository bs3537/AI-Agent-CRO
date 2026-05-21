"""Real-time alert pipeline.

Picks scores above T (per Phase 3 multipliers), applies the PLAN §5
suppression rules, dispatches through configured channels, and persists
one row per alert decision (including suppressed rows for audit).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from ..news.buckets import load_buckets
from ..scorer.multipliers import T
from .channels import Channel, build_channels
from .format import render_alert
from .schema import AlertRecord
from .store import (
    init_outputs_schema,
    pick_alert_candidates,
    recent_alerts_for_suppression,
    save_alert,
)

log = logging.getLogger("sma_monitor.outputs.alerts")

# Suppression window — same (ticker, bucket) needs +20% composite jump
# inside this window to re-alert. PLAN §5 escalation rule.
SUPPRESSION_WINDOW_HOURS = 6
ESCALATION_RATIO = 1.20  # need >20% composite jump within the window to break suppression


# Process every above-T score lacking an alert row. For each, apply the
# suppression check, dispatch via configured channels (unless --dry-run),
# and persist the alert row including suppressed/no-fire decisions.
def run_alerts(
    *,
    min_composite: float | None = None,
    dry_run: bool = False,
    channels: list[Channel] | None = None,
) -> dict:
    init_outputs_schema()
    threshold = min_composite if min_composite is not None else T
    holdings, _missing, _ = latest_joined()
    holdings_by_ticker: dict[str, Holding] = {h.ticker: h for h in holdings}
    buckets = load_buckets()
    if channels is None:
        channels = build_channels(prefer_stdout=False)

    rows = pick_alert_candidates(threshold)
    fired = suppressed = skipped = 0
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

        # --- Suppression check ---
        since = (datetime.now(timezone.utc) - timedelta(hours=SUPPRESSION_WINDOW_HOURS)).isoformat()
        recent = recent_alerts_for_suppression(ticker, row["primary_bucket_id"], since)
        suppress_reason: str | None = None
        if recent:
            max_recent = max(r["composite"] for r in recent)
            if row["composite"] < max_recent * ESCALATION_RATIO:
                suppress_reason = (
                    f"same_bucket_within_{SUPPRESSION_WINDOW_HOURS}h_no_escalation "
                    f"(prev={max_recent:.2f}, this={row['composite']:.2f})"
                )

        record = _build_record(row, holding, bucket.short_name)
        rendered = render_alert(record)
        alerted_at = datetime.now(timezone.utc)
        channels_sent: list[str] = []
        if suppress_reason:
            suppressed += 1
            log.info("alert_suppressed",
                     extra={"ticker": ticker, "bucket": row["primary_bucket_id"],
                            "reason": suppress_reason})
        else:
            if not dry_run:
                for ch in channels:
                    try:
                        ch.send_alert(ticker, rendered)
                        channels_sent.append(ch.name)
                    except Exception as e:
                        log.error("channel_send_failed",
                                  extra={"channel": ch.name, "err": str(e)})
            else:
                channels_sent = ["(dry-run)"]
            fired += 1
            log.info("alert_fired",
                     extra={"ticker": ticker, "bucket": row["primary_bucket_id"],
                            "composite": row["composite"], "channels": channels_sent})

        save_alert(
            score_event_id=row["score_event_id"],
            article_event_id=row["article_event_id"],
            ticker=ticker,
            bucket_id=row["primary_bucket_id"],
            composite=row["composite"],
            severity_of_concern=row["severity_of_concern"],
            rendered_text=rendered,
            channels_sent=channels_sent,
            alerted_at=alerted_at,
            suppressed=bool(suppress_reason),
            suppression_reason=suppress_reason,
        )

    log.info("alerts_summary",
             extra={"fired": fired, "suppressed": suppressed, "skipped": skipped,
                    "threshold": threshold, "dry_run": dry_run})
    return {"fired": fired, "suppressed": suppressed, "skipped": skipped,
            "threshold": threshold}


# Assemble the AlertRecord pydantic model from a sqlite Row + Holding +
# bucket short_name. matched_warning_signs is stored as JSON in the
# red-team table; parsed here for the renderer.
def _build_record(row, holding, bucket_short_name: str) -> AlertRecord:
    try:
        matched = json.loads(row["matched_warning_signs"] or "[]")
    except json.JSONDecodeError:
        matched = []
    return AlertRecord(
        score_event_id=row["score_event_id"],
        article_event_id=row["article_event_id"],
        red_team_event_id=row["red_team_event_id"],
        article_title=row["article_title"] or "",
        article_url=row["article_url"] or "",
        source=row["source"],
        source_tier=row["source_tier"],
        ticker=holding.ticker,
        company_name=holding.company_name,
        pct_nav=holding.pct_nav,
        conviction_tier=int(holding.conviction_tier),
        stage=holding.stage,
        nearest_catalyst_days=holding.nearest_catalyst_days,
        bucket_id=row["primary_bucket_id"],
        bucket_short_name=bucket_short_name,
        composite=row["composite"],
        threshold_band=row["threshold_band"],
        axes=(row["financial_impact"], row["narrative_shift"], row["time_criticality"]),
        scorer_rationale=row["scorer_rationale"] or "",
        bearish_thesis=row["bearish_thesis"],
        severity_of_concern=row["severity_of_concern"],
        matched_warning_signs=matched,
        invalidator=row["invalidator"],
    )

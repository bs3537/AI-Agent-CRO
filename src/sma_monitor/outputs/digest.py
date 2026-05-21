"""Evening digest assembler.

Structured sections from the DB; optional Opus narrative (PLAN §3: "Opus
for the evening synthesis"). The structured digest works without an API
key — narrative is additive.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from ..portfolio.joined import latest_joined
from ..news.buckets import load_buckets
from ..news.store import bucket_activity
from .channels import Channel, build_channels
from .format import render_digest_markdown
from .schema import DigestEvent, DigestSummary, HoldingSnapshot
from .store import (
    bucket_ticker_hits_today,
    init_outputs_schema,
    save_digest,
    todays_digest_events,
)

log = logging.getLogger("sma_monitor.outputs.digest")

# Opus 4.7 for the synthesis paragraph (PLAN §3). Higher cost is acceptable
# because there's exactly one synthesis per day.
OPUS_MODEL = "claude-opus-4-7"
# Window for the bucket-coverage audit + silent-bucket flag.
SILENT_DAYS = 14


# Assemble and dispatch one day's digest. Pulls today's scores, the joined
# holdings snapshot, and bucket-coverage stats; renders markdown; sends
# via every configured channel; persists the digest row.
def assemble_digest(
    *,
    date_iso: str | None = None,
    with_narrative: bool = False,
    skip_opus: bool = False,
    api_key: str | None = None,
    channels: list[Channel] | None = None,
) -> dict:
    init_outputs_schema()
    if date_iso is None:
        date_iso = datetime.now(timezone.utc).date().isoformat()
    if channels is None:
        channels = build_channels(prefer_stdout=False)

    holdings, _missing, _ = latest_joined()
    holdings_index: dict[str, dict] = {
        h.ticker: {
            "pct_nav": h.pct_nav, "conviction_tier": int(h.conviction_tier),
            "stage": h.stage, "nearest_catalyst_days": h.nearest_catalyst_days,
            "has_overdue_catalyst": h.has_overdue_catalyst,
        } for h in holdings
    }
    buckets = load_buckets()

    # --- Today's events grouped by ticker ---
    events_rows = todays_digest_events(date_iso)
    events_by_ticker: dict[str, list[DigestEvent]] = {}
    tickers_hit_today: set[str] = set()
    for row in events_rows:
        try:
            matched = json.loads(row["matched_warning_signs"] or "[]")
        except (TypeError, json.JSONDecodeError):
            matched = []
        bucket = buckets.get(row["primary_bucket_id"])
        ev = DigestEvent(
            ticker=row["ticker"],
            composite=row["composite"],
            threshold_band=row["threshold_band"],
            bucket_id=row["primary_bucket_id"],
            bucket_short_name=bucket.short_name if bucket else str(row["primary_bucket_id"]),
            title=row["title"] or "",
            url=row["url"] or "",
            source=row["source"],
            source_tier=row["source_tier"],
            axes=(row["financial_impact"], row["narrative_shift"], row["time_criticality"]),
            scorer_rationale=row["scorer_rationale"] or "",
            bearish_thesis=row["bearish_thesis"],
            severity_of_concern=row["severity_of_concern"],
            matched_warning_signs=matched,
        )
        events_by_ticker.setdefault(ev.ticker, []).append(ev)
        tickers_hit_today.add(ev.ticker)

    # --- Quiet holdings ---
    quiet = [
        HoldingSnapshot(
            ticker=h.ticker, pct_nav=h.pct_nav,
            conviction_tier=int(h.conviction_tier), stage=h.stage,
            nearest_catalyst_days=h.nearest_catalyst_days,
            has_overdue_catalyst=h.has_overdue_catalyst,
        )
        for h in holdings if h.ticker not in tickers_hit_today
    ]

    # --- Concentrations ---
    concentrations = [(bid, n) for bid, n in bucket_ticker_hits_today(date_iso) if n >= 2]

    # --- Coverage audit ---
    coverage = bucket_activity(days=SILENT_DAYS)
    silent = [bid for bid in sorted(buckets) if coverage.get(bid, 0) == 0]

    summary = DigestSummary(
        date=date_iso,
        events_by_ticker=events_by_ticker,
        quiet_holdings=quiet,
        bucket_concentrations=concentrations,
        coverage_audit=coverage,
        silent_buckets=silent,
        narrative=None,
        assembled_at=datetime.now(timezone.utc),
    )

    # Render once to feed the synthesizer (if used)
    structured_md = render_digest_markdown(summary, holdings_index)
    used_narrative = False
    if with_narrative:
        narrative = _synthesize_narrative(
            structured_md, summary,
            api_key=api_key, skip_opus=skip_opus,
        )
        if narrative:
            summary.narrative = narrative
            used_narrative = True

    rendered = render_digest_markdown(summary, holdings_index)

    file_path = None
    for ch in channels:
        try:
            res = ch.send_digest(date_iso, rendered)
            if res is not None:
                file_path = str(res)
        except Exception as e:
            log.error("channel_send_failed",
                      extra={"channel": ch.name, "err": str(e)})

    n_events = sum(len(v) for v in events_by_ticker.values())
    save_digest(
        date=date_iso, rendered_md=rendered, n_events=n_events,
        used_narrative=used_narrative, file_path=file_path,
        assembled_at=summary.assembled_at,
    )
    log.info("digest_assembled",
             extra={"date": date_iso, "n_events": n_events,
                    "tickers": len(events_by_ticker),
                    "quiet": len(quiet), "silent_buckets": silent,
                    "file": file_path, "narrative": used_narrative})
    return {"date": date_iso, "n_events": n_events, "file_path": file_path,
            "narrative": used_narrative}


# Try Opus first; fall back to a deterministic template paragraph if Opus
# is unavailable, fails, or the budget cascade has told us to skip the
# Opus call (cascade step 2 at 75% spend). The template uses only the
# structured summary so it stays meaningful without LLM access.
def _synthesize_narrative(
    structured_md: str,
    summary: DigestSummary,
    *,
    api_key: str | None,
    skip_opus: bool = False,
) -> str | None:
    if not api_key or skip_opus:
        return _template_narrative(summary)
    try:
        import anthropic
    except ImportError:
        return _template_narrative(summary)

    system_prompt = (
        "You synthesize SMA news digests for a biotech-heavy book. You write "
        "ONE neutral paragraph (4-7 sentences). No directional verbs "
        "(no buy/sell/trim/add). Pattern-cite when a red-team match is shown. "
        "Highlight the top 1-3 items and the most notable bucket-level "
        "concentration. End with the most actionable watch item by catalyst "
        "proximity if any."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=600,
            system=[{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user",
                       "content": f"Today's structured digest:\n\n{structured_md}"
                                  f"\n\nWrite the synthesis paragraph."}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return _strip_markdown_fence(text).strip()
    except Exception as e:
        log.warning("opus_narrative_failed", extra={"err": str(e)})
        return _template_narrative(summary)


# Strip leading/trailing markdown fences from a synthesis response so the
# paragraph fits cleanly into the digest markdown.
def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text


# Deterministic narrative generator used when Opus isn't available. Picks
# the lead item, the top concentration, the silent-bucket list, and any
# near-catalyst quiet holdings.
def _template_narrative(summary: DigestSummary) -> str:
    """Offline narrative built from the structured payload only."""
    n_tickers = len(summary.events_by_ticker)
    n_events = sum(len(v) for v in summary.events_by_ticker.values())
    if n_events == 0:
        return ("No scored events for the day. Quiet holdings dominate; "
                "bucket-coverage footer below highlights any ingestion gaps.")

    top: list[tuple[str, float, str]] = []
    for t, evs in summary.events_by_ticker.items():
        top_ev = max(evs, key=lambda e: e.composite)
        top.append((t, top_ev.composite, top_ev.title))
    top.sort(key=lambda x: -x[1])

    parts = [
        f"{n_events} scored events across {n_tickers} holding(s) today.",
    ]
    if top:
        leader = top[0]
        parts.append(f"Lead item: {leader[0]} at composite {leader[1]:.1f} — \"{leader[2][:90]}\".")
    if summary.bucket_concentrations:
        bid, n = summary.bucket_concentrations[0]
        parts.append(f"Concentration to watch: bucket #{bid} hit {n} holdings.")
    if summary.silent_buckets:
        parts.append(
            f"Silent buckets in last {SILENT_DAYS} days "
            f"({len(summary.silent_buckets)}): "
            + ", ".join(f"#{b}" for b in summary.silent_buckets) + "."
        )
    near = [h for h in summary.quiet_holdings
            if h.nearest_catalyst_days is not None and h.nearest_catalyst_days <= 14]
    if near:
        parts.append("Quiet holdings with near catalysts: "
                     + ", ".join(f"{h.ticker} ({h.nearest_catalyst_days}d)" for h in near) + ".")
    return " ".join(parts)

"""Rendering: alerts (mobile-glanceable text), digest (markdown).

PLAN.MD §5: title line carries ticker + bucket + severity; body carries
context. Plain text works as the SMS/email-text body AND as a markdown
block (with a leading `### ` if used inside the digest).
"""
from __future__ import annotations

from .schema import AlertRecord, DigestEvent, DigestSummary


def _nearest_label(days: int | None) -> str:
    if days is None:
        return "—"
    if days < 0:
        return f"{abs(days)}d overdue"
    return f"{days}d"


def _patterns_label(matches: list[dict]) -> str:
    if not matches:
        return "—"
    parts = [f"{m['id']} ({m['fit_strength']:.2f})" for m in matches[:3]]
    extra = f", +{len(matches) - 3}" if len(matches) > 3 else ""
    return ", ".join(parts) + extra


def render_alert(record: AlertRecord) -> str:
    """Plain text. Title line first, then context. Used by all channels."""
    sev = record.severity_of_concern
    sev_label = f"SEV {sev}" if sev is not None else "SEV –"
    title = (
        f"[{record.ticker}] #{record.bucket_id} {record.bucket_short_name} | "
        f"{sev_label} | comp {record.composite:.1f} ({record.threshold_band})"
    )

    f, n, t = record.axes
    lines = [
        title,
        "",
        record.article_title.strip(),
        "",
        f"Pos: {record.pct_nav * 100:.2f}%NAV  T{record.conviction_tier} {record.stage}",
        f"Catalyst: {_nearest_label(record.nearest_catalyst_days)}",
        f"Source:   {record.source or '—'} (tier {record.source_tier or '–'})",
        "",
        f"Scorer F/N/T: {f:.1f} / {n:.1f} / {t:.1f}",
    ]
    if record.scorer_rationale:
        lines.append(f"  \"{record.scorer_rationale}\"")

    if record.bearish_thesis:
        lines.append("")
        lines.append(
            f"Red team [SEV {record.severity_of_concern}] — patterns: "
            f"{_patterns_label(record.matched_warning_signs)}"
        )
        lines.append(f"  \"{record.bearish_thesis}\"")
        if record.invalidator:
            lines.append(f"  Invalidator: {record.invalidator}")
    else:
        lines.append("")
        lines.append("Red team: no pass available for this score.")

    lines.append("")
    lines.append(f"Link: {record.article_url}")
    return "\n".join(lines)


# --- Digest markdown ---------------------------------------------------------


def render_digest_markdown(summary: DigestSummary, holdings_index: dict[str, dict]) -> str:
    """Assemble the full digest as markdown. holdings_index keyed by ticker."""
    parts: list[str] = []
    parts.append(f"# SMA Daily Digest — {summary.date}")
    parts.append("")
    parts.append(f"*Assembled {summary.assembled_at.isoformat()}*")
    parts.append("")

    # --- Today's events grouped by ticker, ranked by composite ---
    parts.append("## Today's events")
    parts.append("")
    if not summary.events_by_ticker:
        parts.append("*(no scored events today)*")
        parts.append("")
    else:
        for ticker in sorted(
            summary.events_by_ticker,
            key=lambda t: -max(e.composite for e in summary.events_by_ticker[t]),
        ):
            ctx = holdings_index.get(ticker, {})
            pct = ctx.get("pct_nav", 0.0) * 100
            tier = ctx.get("conviction_tier", "–")
            stage = ctx.get("stage", "—")
            nearest = ctx.get("nearest_catalyst_days")
            parts.append(
                f"### {ticker}  ({pct:.2f}% NAV, T{tier} {stage}, "
                f"catalyst {_nearest_label(nearest)})"
            )
            for ev in summary.events_by_ticker[ticker]:
                parts.append(_render_event_block(ev))
            parts.append("")

    # --- Quiet holdings ---
    parts.append("## Quiet holdings")
    parts.append("")
    if not summary.quiet_holdings:
        parts.append("*(every holding had at least one scored event today)*")
    else:
        for h in summary.quiet_holdings:
            flag = ""
            if h.has_overdue_catalyst:
                flag = "  ⚠ overdue catalyst — review sidecar"
            elif h.nearest_catalyst_days is not None and h.nearest_catalyst_days <= 14:
                flag = f"  ⚠ catalyst in {h.nearest_catalyst_days}d"
            parts.append(
                f"- **{h.ticker}** ({h.pct_nav * 100:.2f}% NAV, T{h.conviction_tier} {h.stage}){flag}"
            )
    parts.append("")

    # --- Concentrations ---
    parts.append("## Risk concentrations (today)")
    parts.append("")
    if not summary.bucket_concentrations:
        parts.append("*(no bucket has multiple holdings hit today)*")
    else:
        for bid, n in summary.bucket_concentrations[:3]:
            parts.append(f"- Bucket #{bid}: {n} holding(s) hit today")
    parts.append("")

    # --- Coverage audit ---
    parts.append("## Bucket coverage audit (last 14 days)")
    parts.append("")
    if not summary.coverage_audit and not summary.silent_buckets:
        parts.append("*(no bucket activity in window)*")
    else:
        for bid, n in sorted(summary.coverage_audit.items()):
            parts.append(f"- Bucket #{bid}: {n} articles")
        if summary.silent_buckets:
            parts.append("")
            parts.append(
                "**Silent (≥14 days with no articles, possible ingestion gap):** "
                + ", ".join(f"#{b}" for b in sorted(summary.silent_buckets))
            )
    parts.append("")

    # --- Narrative ---
    if summary.narrative:
        parts.append("## Synthesis")
        parts.append("")
        parts.append(summary.narrative.strip())
        parts.append("")

    # --- Feedback footer ---
    parts.append("---")
    parts.append("*To mark feedback:* "
                 "`python -m sma_monitor.outputs mark <event_id> useful|noise`  "
                 "*or* `mark-missed --ticker T --description \"…\"`")
    return "\n".join(parts)


def _render_event_block(ev: DigestEvent) -> str:
    f, n, t = ev.axes
    sev_label = f"SEV {ev.severity_of_concern}" if ev.severity_of_concern is not None else "SEV –"
    lines = [
        f"- **[comp {ev.composite:.1f} / {ev.threshold_band} / {sev_label}]** "
        f"#{ev.bucket_id} {ev.bucket_short_name} — {ev.title.strip()}",
        f"  [{ev.source or 'source –'} t{ev.source_tier or '–'}] {ev.url}",
        f"  Scorer F/N/T: {f:.1f}/{n:.1f}/{t:.1f} — {ev.scorer_rationale}",
    ]
    if ev.bearish_thesis:
        if ev.matched_warning_signs:
            cites = ", ".join(m["id"] for m in ev.matched_warning_signs[:3])
            lines.append(f"  Red team [{sev_label}] cites: {cites}")
        else:
            lines.append(f"  Red team [{sev_label}]: no pattern match")
        lines.append(f"  → \"{ev.bearish_thesis.strip()}\"")
    return "\n".join(lines)

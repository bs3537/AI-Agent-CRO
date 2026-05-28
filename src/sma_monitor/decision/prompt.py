"""Thesis-drift monitor prompts.

System message defines the verdict as a thesis-integrity signal (not a trade
instruction) and keeps the note in the same neutral, observational voice the
red team uses. User message bundles the long thesis, any uploaded thesis-doc
text, the scored articles, the red-team bear cases, catalysts, open P&L, and
(when present) FMP metrics — everything the model needs to judge whether the
original thesis still holds.
"""
from __future__ import annotations

from .schema import DecisionCandidate

# --- System prompt -----------------------------------------------------------

# Defines the role, the verdict semantics, and the voice. The verdict is a
# controlled signal about thesis integrity — HOLD/WATCH/SELL describe how much
# the evidence has drifted from the stated thesis, not a brokerage order. The
# note stays observational (no hype, no generic bear boilerplate).
_SYSTEM = """\
You are a thesis-drift monitor for a biotech-heavy SMA. For ONE existing long \
position you are given (1) the manager's stated long thesis, (2) any uploaded \
thesis documents, and (3) everything the monitoring pipeline has ingested on \
the name: severity-scored articles, red-team bear cases with cited \
warning-sign patterns, upcoming catalysts, financial metrics, and the \
position's open P&L. Your single job: judge how far the EVIDENCE has drifted \
from the THESIS, and say so.

VERDICT — a thesis-integrity signal, not a trade instruction:
- "hold"  → thesis intact. No ingested evidence materially contradicts it.
- "watch" → thesis under pressure. Real evidence pushes against the thesis but \
is not yet decisive; warrants closer monitoring.
- "sell"  → thesis materially broken. Evidence directly invalidates a load- \
bearing assumption of the thesis (e.g. a failed primary endpoint, CRL on the \
lead asset, going-concern language).

RULES:
- Ground every claim in the evidence actually provided. Do NOT invent events. \
If the name is quiet and the thesis is intact, "hold" is the correct answer — \
absence of bad news is not a reason to escalate.
- Weight the red team's severity_of_concern and the scorer's composite. A \
red-team severity of 4–5 means the thesis is at least under pressure.
- The note is 4–5 lines of plain English: restate what the thesis rests on, \
state what the ingested evidence does to that thesis, and end with the one \
observation that would change your verdict. Neutral and specific — no \
"moon"/"crush" hype, no generic short narrative unsupported by the evidence.
- drivers: 2–5 short phrases naming the CONCRETE evidence behind the verdict \
(e.g. "DSMB safety hold on lead asset", "channel_inventory_build pattern", \
"runway < 12 months"). Empty list only when there is genuinely no evidence.
- Calibrate confidence to how much evidence you actually have.
"""

# Source-provenance policy injected into the system prompt: how Codex must weigh
# evidence by where it came from, plus the rule that API-derived data needs Brave
# corroboration. Mirrors news/source_policy.py (the single source of truth).
_SOURCE_POLICY = """\
SOURCE POLICY — weigh evidence by where it came from:
- Financials: trust SEC filings (primary/regulatory) over FMP (a third-party \
data API). A financial claim resting only on FMP is provisional.
- Biomedical evidence: trust PubMed, ClinicalTrials.gov, and regulatory/primary \
sources over Semantic Scholar (an aggregator API). For non-biomedical names, \
trust reputable web sources over Semantic Scholar.
- VERIFICATION: data from external APIs (FMP, Semantic Scholar) is reliable only \
when an independent web source corroborates it. Treat uncorroborated API-derived \
figures or claims as LOW CONFIDENCE — you may report them, but do NOT escalate \
the verdict (watch/sell) on uncorroborated API data alone, and name the missing \
corroboration in the note.
"""

# Output shape echoed in the system prompt so the model emits exactly the JSON
# the engine's DECISION_OUTPUT_SCHEMA validates. color is derived from verdict
# by the engine and is intentionally NOT requested here.
_OUTPUT_SCHEMA = """\
OUTPUT — valid JSON only, no preamble, no markdown fences:
{
  "verdict": "hold" | "watch" | "sell",
  "note": "<4-5 line plain-English thesis-drift assessment>",
  "drivers": ["<short evidence phrase>", ...],
  "confidence": <0-1 decimal>
}
"""


# Assemble the full system prompt: role + verdict semantics + output schema.
def build_system_prompt() -> str:
    return f"{_SYSTEM}\n{_SOURCE_POLICY}\n{_OUTPUT_SCHEMA}"


# Build the user-side message for one candidate. Compact labeled sections so
# the model parses reliably; evidence is pre-truncated by the engine. Empty
# slots (no docs / no FMP yet) render as explicit placeholders, never blanks.
def build_user_message(c: DecisionCandidate) -> str:
    pnl = _fmt_pnl(c.open_pnl, c.pnl_pct)
    nearest = (
        f"{c.nearest_catalyst_days}d"
        if c.nearest_catalyst_days is not None
        else "no catalyst on file"
    )
    if c.has_overdue_catalyst:
        nearest += " (has overdue catalyst)"

    docs = c.thesis_doc_text.strip() or "(no thesis documents uploaded)"
    catalysts = (
        "\n".join(f"    - {s}" for s in c.catalysts) if c.catalysts else "    (none on file)"
    )
    scores = _fmt_scores(c.scores)
    bears = _fmt_bears(c.bears)
    fmp = _fmt_fmp(c.fmp_metrics)
    fmp_check = _fmt_fmp_corroboration(c.fmp_corroboration)

    return f"""\
POSITION
  Ticker:           {c.ticker} ({c.company_name or '—'})
  Stage:            {c.stage}
  Conviction tier:  {c.conviction_tier}
  % NAV:            {c.pct_nav * 100:.2f}%
  Open P&L:         {pnl}
  Nearest catalyst: {nearest}

THESIS (long, as stated by the manager)
{_indent(c.thesis.strip() or '(no thesis on file)')}

THESIS DOCUMENTS (uploaded)
{_indent(docs)}

UPCOMING CATALYSTS
{catalysts}

SCORED ARTICLES (Phase 3 — highest composite first; peak composite {c.max_composite:.1f})
{scores}

RED-TEAM BEAR CASES (Phase 4 — peak severity {c.max_severity}/5)
{bears}

FINANCIAL METRICS (FMP — third-party API; corroborate before trusting)
{fmp}
{fmp_check}

Assess thesis drift for {c.ticker}. Output JSON only.
"""


# Render the open P&L line; "—" when cost basis is unknown.
def _fmt_pnl(open_pnl: float | None, pnl_pct: float | None) -> str:
    if open_pnl is None:
        return "— (cost basis unknown)"
    pct = f"{pnl_pct * 100:+.1f}%" if pnl_pct is not None else "—"
    return f"{open_pnl:+,.0f} ({pct})"


# Render the scored-article evidence block (or a placeholder when empty).
def _fmt_scores(scores) -> str:
    if not scores:
        return "  (no scored articles)"
    lines = []
    for s in scores:
        lines.append(
            f"  - [#{s.primary_bucket_id} composite {s.composite:.1f} {s.threshold_band}] "
            f"{s.title[:90]}"
        )
        if s.rationale:
            lines.append(f"      scorer: {s.rationale.strip()[:180]}")
    return "\n".join(lines)


# Render the red-team bear-case block (or a placeholder when empty).
def _fmt_bears(bears) -> str:
    if not bears:
        return "  (no red-team passes)"
    lines = []
    for b in bears:
        pats = ", ".join(b.matched_patterns) or "no catalog pattern"
        lines.append(f"  - [severity {b.severity_of_concern}/5 — {pats}] {b.title[:80]}")
        lines.append(f"      bear: {b.bearish_thesis.strip()[:200]}")
        if b.invalidator:
            lines.append(f"      invalidator: {b.invalidator.strip()[:160]}")
    return "\n".join(lines)


# Render FMP metrics as key: value lines, or a placeholder (W2 not yet wired).
def _fmt_fmp(metrics: dict | None) -> str:
    if not metrics:
        return "  (financial metrics unavailable)"
    return "\n".join(f"  - {k}: {v}" for k, v in metrics.items())


# Render the Brave web-corroboration of the FMP data (the source-policy
# verification): whether an independent source backs the API figures, plus the
# top corroborating sources with their credibility tiers, for Codex to weigh.
def _fmt_fmp_corroboration(corr: dict | None) -> str:
    if not corr:
        return "  Web corroboration (Brave): not checked."
    status = "corroborated" if corr.get("corroborated") else \
        "NOT corroborated by an independent source"
    lines = [f"  Web corroboration (Brave): {status}."]
    for s in corr.get("sources", []):
        lines.append(f"    - [tier {s.get('tier')}] {(s.get('title') or '')[:70]} — {s.get('url', '')}")
    return "\n".join(lines)


# Indent a possibly-multiline block by two spaces for the labeled layout.
def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines()) or "  —"

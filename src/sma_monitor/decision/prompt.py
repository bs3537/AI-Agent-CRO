"""Thesis-drift monitor prompts.

System message defines the grade as a thesis-integrity signal (not a trade
instruction) and keeps the note in the same neutral, observational voice the
red team uses. User message bundles the long thesis, any uploaded thesis-doc
text, the scored articles, the red-team bear cases, catalysts, open P&L,
technical trend, and (when present) FMP metrics — everything the model needs
to judge whether the original thesis still holds.
"""
from __future__ import annotations

from .schema import DecisionCandidate

# --- System prompt -----------------------------------------------------------

# Defines the role, the grade semantics, and the voice. The grade is a
# controlled signal about thesis integrity; action is derived downstream.
# The note stays observational (no hype, no generic bear boilerplate).
_SYSTEM = """\
You are a thesis-drift monitor for a biotech-heavy SMA. For ONE existing long \
position you are given (1) the manager's stated long thesis, (2) any uploaded \
thesis documents, and (3) everything the monitoring pipeline has ingested on \
the name: severity-scored articles, red-team bear cases with cited \
warning-sign patterns, upcoming catalysts, financial metrics, and the \
position's open P&L and daily price trend. Your single job: judge how far the EVIDENCE has drifted \
from the THESIS, and say so.

GRADE — a thesis-integrity signal, not a trade instruction:
- "A" → clean hold. Thesis intact; routine monitoring.
- "B" → hold/monitor. Thesis mostly intact, but at least one issue deserves attention.
- "C" → hold/on watch. Thesis under real pressure; active review required.
- "D" → sell review. Evidence materially breaks a load-bearing assumption of the thesis \
(e.g. a failed primary endpoint, CRL on the lead asset, going-concern language).

AUTHORITY:
- Your `llm_grade` is the final portfolio-facing grade when valid. The \
deterministic scorecard, red-team severity, EMA20 status, and source-quality \
checks are decision inputs for you to weigh, not hard post-processing overrides.
- If you disagree with a deterministic risk signal, keep the grade you believe \
is right and explain the disagreement in the note or drivers.

RULES:
- Ground every claim in the evidence actually provided. Do NOT invent events. \
If the name is quiet and the thesis is intact, "A" is the correct answer — \
absence of bad news is not a reason to escalate.
- Thesis authority matters. If the thesis is an auto scaffold/stub, treat it as \
a provisional working thesis and you may refine the note/kill-switch language \
from the provided evidence. If the manager has entered a thesis, that \
manager-entered thesis is the controlling thesis. Uploaded documents are \
supporting context: use them to add detail, clauses, and kill switches, but do \
not let an older uploaded document override the manager-entered thesis unless \
the conflict is explicit and material.
- Weight the red team's severity_of_concern and the scorer's composite, but use \
judgment about source quality and whether the evidence truly hits a \
load-bearing thesis clause.
- Use the 20-day EMA as a risk-management/attention input. A close below EMA20 \
can support a downgrade when fundamentals also weaken, but a technical break \
alone should not justify D.
- Use unrealized P&L as a risk-management/attention input. A loss of 10% or \
more is a warning sign because the market may be rejecting the thesis; it can \
support a downgrade when paired with fundamental, catalyst, technical, or \
evidence weakness, but it does not automatically mean sell.
- The note is 4–5 lines of plain English: restate what the thesis rests on, \
state what the ingested evidence does to that thesis, and end with the one \
observation that would change your grade. Neutral and specific — no \
"moon"/"crush" hype, no generic short narrative unsupported by the evidence.
- drivers: 2–5 short phrases naming the CONCRETE evidence behind the grade \
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
figures or claims as LOW CONFIDENCE — you may report them, but do NOT downgrade \
the grade on uncorroborated API data alone, and name the missing \
corroboration in the note.
"""

# Output shape echoed in the system prompt so the model emits exactly the JSON
# the engine's DECISION_OUTPUT_SCHEMA validates. Action/verdict/color are
# derived by the engine and are intentionally NOT requested here.
_OUTPUT_SCHEMA = """\
OUTPUT — valid JSON only, no preamble, no markdown fences:
{
  "llm_grade": "A" | "B" | "C" | "D",
  "thesis_clause_impacts": [
    {
      "clause_id": "<short id or 'free_text_thesis'>",
      "impact": "none" | "low" | "medium" | "high" | "critical",
      "evidence": "<short source-grounded explanation>"
    }
  ],
  "hard_breaker": {
    "present": true | false,
    "type": "<breaker type or empty string>",
    "evidence": "<source-grounded explanation or empty string>"
  },
  "technical_assessment": {
    "uses_ema20": true,
    "interpretation": "<how the close vs 20-day EMA affects confidence>",
    "should_affect_grade": "none" | "cap_A_at_B" | "push_one_notch" |
      "no_sell_without_fundamental_confirmation"
  },
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
    technical = _fmt_technical(c)
    pnl_warning = _fmt_pnl_warning(c.pnl_pct)
    thesis_source, thesis_authority = _thesis_authority(c.thesis)

    return f"""\
POSITION
  Ticker:           {c.ticker} ({c.company_name or '—'})
  Stage:            {c.stage}
  Conviction tier:  {c.conviction_tier}
  % NAV:            {c.pct_nav * 100:.2f}%
  Open P&L:         {pnl}
  P&L warning:      {pnl_warning}
  Nearest catalyst: {nearest}

THESIS AUTHORITY
  Source:           {thesis_source}
  Rule:             {thesis_authority}

PRIMARY THESIS
{_indent(c.thesis.strip() or '(no thesis on file)')}

SUPPORTING THESIS DOCUMENTS (uploaded)
{_indent(docs)}

UPCOMING CATALYSTS
{catalysts}

SCORED ARTICLES (Phase 3 — highest composite first; peak composite {c.max_composite:.1f})
{scores}

RED-TEAM BEAR CASES (Phase 4 — peak severity {c.max_severity}/5)
{bears}

TECHNICAL TREND (daily close vs 20-day EMA)
{technical}

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


def _fmt_pnl_warning(pnl_pct: float | None) -> str:
    if pnl_pct is None:
        return "not available"
    if pnl_pct <= -0.10:
        return f"WARNING — unrealized loss {pnl_pct * 100:.1f}% is at/above the 10% loss threshold"
    return "none — unrealized loss is better than -10%"


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
        title = (s.get("title") or "")[:70]
        lines.append(f"    - [tier {s.get('tier')}] {title} — {s.get('url', '')}")
    return "\n".join(lines)


def _fmt_technical(c: DecisionCandidate) -> str:
    tech = c.technical
    if not tech or tech.technical_state == "no_price_data":
        return "  (price history unavailable or too short for EMA20)"
    pct = (
        f"{tech.price_vs_ema20_pct * 100:+.1f}%"
        if tech.price_vs_ema20_pct is not None
        else "n/a"
    )
    slope = (
        f"{tech.ema20_slope_5d * 100:+.1f}%"
        if tech.ema20_slope_5d is not None
        else "n/a"
    )
    return "\n".join([
        f"  - state: {tech.technical_state}",
        f"  - latest close: {tech.latest_close}",
        f"  - EMA20: {tech.latest_ema20}",
        f"  - distance vs EMA20: {pct}",
        f"  - consecutive closes below EMA20: {tech.consecutive_below_ema20}",
        f"  - EMA20 5-day slope: {slope}",
    ])


def _thesis_authority(thesis: str) -> tuple[str, str]:
    if _is_placeholder_thesis(thesis):
        return (
            "auto_scaffold_fallback",
            "Use as a provisional working thesis; refine note and kill switches from evidence.",
        )
    return (
        "manager_entered_primary",
        "Manager thesis controls; uploaded docs support it but should not override it.",
    )


def _is_placeholder_thesis(thesis: str) -> bool:
    text = thesis.strip().upper()
    return text.startswith("STUB") or text.startswith("PLACEHOLDER")


# Indent a possibly-multiline block by two spaces for the labeled layout.
def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines()) or "  —"

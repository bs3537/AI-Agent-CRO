"""Scoring rubric. Three axes, each 0–10.

Phase 3 open decision (per PLAN.MD): rubric anchors per axis. Tune in Phase 7
based on alert precision. The text in RUBRIC_TEXT feeds Claude's system
message; data/scores/rubric.md is the human-readable mirror — keep them in
sync when tuning.
"""
from __future__ import annotations

# The full rubric Claude sees as part of the system prompt. Editing this
# changes scoring behavior — bump MULTIPLIERS_VERSION when you change it so
# existing scores get cleanly recomputed at the new rubric.
RUBRIC_TEXT = """\
THREE AXES (each 0–10, decimals allowed):

financial_impact — direct or expected $/value implication for THIS holding
  0   No financial implication
  3   Minor — small revenue/cost line item, contained
  5   Moderate — meaningful but contained to one product / one quarter
  8   Major — material to the next 1–2 quarters of earnings or guidance
  10  Existential — solvency, going-concern, or thesis-defining

narrative_shift — change to the long-thesis as stated in the sidecar
  0   No update — already priced in or off-thesis
  3   Watch item — no change yet, but worth tracking
  5   Update to one component of the thesis (e.g., one indication, one site)
  8   Modification to the central thesis (pivot, derisking, or new risk)
  10  Thesis break — the long view no longer holds

time_criticality — how soon does this need attention
  0   No action ever needed
  3   This quarter
  5   This month
  8   This week
  10  Immediate — today or tomorrow

BUCKET-AWARENESS:
- A bucket #12 (microstructure/flow) event rarely scores above 5 on
  narrative_shift — flow data describes positioning, not thesis.
- A bucket #1 (clinical) event near a catalyst (≤14d) is more often a 7+ on
  time_criticality.
- A bucket #4 (commercial/revenue quality) event scores narrative_shift
  higher for commercial-stage holdings than for clinical-stage names.
- A bucket #7 (capital structure) event scores financial_impact higher for
  clinical-stage holdings (no commercial revenue cushion).

CONFIDENCE: a single 0–1 value indicating how confident you are in this
score given the article text. Low confidence (<0.4) often means the article
is too thin, off-topic, or ambiguous.

RATIONALE: one sentence, neutral and observational. Do not use directional
verbs like "buy", "sell", "trim", "add". Describe what the article shifts.

OUTPUT FORMAT — emit valid JSON only, no preamble, no markdown fences:
{
  "financial_impact": <0-10>,
  "narrative_shift": <0-10>,
  "time_criticality": <0-10>,
  "rationale": "<one sentence>",
  "confidence": <0-1>
}
"""

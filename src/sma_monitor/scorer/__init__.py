"""Phase 3 — Severity scorer (neutral pass).

Modules:
  rubric        — anchor definitions for the three axes (financial_impact,
                  narrative_shift, time_criticality); 0–10 scale
  multipliers   — bucket weights, conviction tier mults, catalyst proximity
                  boost, stage interaction, log-scale position weight,
                  thresholds T and T₂, MULTIPLIERS_VERSION
  schema        — AxisScores, CompositeScore (Pydantic)
  prompt        — system + user prompt builders (system message cacheable)
  claude_client — Anthropic Sonnet 4.6 wrapper with JSON extraction
  heuristic     — deterministic offline scorer (Phase 6 cost-degrade fallback)
  store         — scores table, save_score, recent_scores
  pipeline      — pick unscored (article × ticker) pairs → score → persist
  calibration   — load calibration_set.yaml → score → compare to expected_band
"""

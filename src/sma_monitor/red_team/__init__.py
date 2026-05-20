"""Phase 4 — Red team pass.

Modules:
  catalog        — load + validate the warning-signs library
                   (data/warning_signs/catalog.yaml)
  schema         — RedTeamCandidate / RedTeamResult / MatchedWarningSign
  prompt         — system embeds the catalog with cache_control;
                   voice constraints (pattern-cite, no sell verbs)
  claude_client  — Sonnet 4.6 wrapper with JSON extraction
  heuristic      — offline fallback: keyword overlap against catalog
  store          — red_team_passes table + persistence
  pipeline       — pick scores with composite >= T₂ → run → persist
"""

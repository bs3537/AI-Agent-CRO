"""Phase 1 — Portfolio state layer.

Modules:
  schema  — Pydantic models for Position, Sidecar (manual metadata), Holding (joined view)
  sidecar — Read/write per-ticker YAML sidecars in data/portfolio/sidecar/
  flex    — IBKR Flex Web Service client + XML parser
  store   — SQLite persistence for pulls and positions
  joined  — Build Holding view (positions ⨝ sidecar); canonical input for Phase 2+
"""

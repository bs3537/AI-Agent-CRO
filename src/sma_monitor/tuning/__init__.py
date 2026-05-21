"""Phase 7 — Tuning loop.

Reads-only analytics over the artifacts produced by Phases 1–6. PLAN.MD §7
calls for: per-bucket alert precision (measured per-bucket, not globally),
weight tuning based on what got ignored vs. acted on, warning-signs library
growth from misses, quarterly conviction-tier re-validation, plus the
bucket-level architecture questions (#10 vs #1+#5 overlap, #11 → portfolio
overlay, cybersecurity → own bucket).

Modules:
  precision         — alert_precision_by_bucket()
  weights           — suggest_weight_adjustments()
  library_growth    — missed_events → candidate warning-sign YAML
  conviction_review — sidecar staleness + tier distribution
  bucket_review     — silent buckets, noise-only, PLAN §7 questions
  report            — markdown report assembler
  __main__          — CLI
"""

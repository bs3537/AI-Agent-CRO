"""Phase 2 — News ingestion.

Modules:
  buckets       — load + validate the 12-bucket taxonomy from data/factor_buckets/
  query         — build per-holding and sector queries from sidecar + bucket terms
  exa_client    — Exa search adapter (+ --from-file replay)
  source_tiers  — URL → priority tier 1 (highest) … 6 (lowest)
  tagger        — keyword-based bucket tagging and ticker matching
  store         — SQLite persistence for articles, tags, poll records
  pipeline      — orchestration: holdings × buckets → query → tag → persist
"""

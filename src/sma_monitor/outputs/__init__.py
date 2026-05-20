"""Phase 5 — Outputs.

Modules:
  schema      — AlertRecord (rendered alert + metadata), DigestEvent
  store       — alerts / digests / feedback / missed_events tables
  format      — mobile-glanceable alert text + markdown blocks
  channels    — FileChannel (always-on archive) / StdoutChannel / EmailChannel
  alerts      — pick scores ≥ T, apply suppression, dispatch, persist
  digest      — assemble structured + Opus-narrative evening digest
  feedback    — mark alerts as useful / noise; record missed events
  __main__    — CLI: alerts / digest / show-alerts / show-digest /
                mark / mark-missed / feedback-list
"""

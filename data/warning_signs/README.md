# Warning signs library

The red team (Phase 4) cites entries from `catalog.yaml` when arguing the
short case against a holding. Every entry must have a real historical
example — invented patterns corrupt the library.

## Library-growth discipline (PLAN.MD §4)

> "Warning signs are added from misses in Phase 7, not invented
> prospectively. Every entry must have a real historical example."

What this means in practice:

1. **Initial seed (Phase 4)**: ~25 patterns, density-weighted per PLAN —
   bucket #4 has the most (~7), buckets #1/#2/#8 each get 3-5, others 1-2.
   The seed examples reference well-known public episodes (Valeant/Philidor,
   anti-amyloid class, IRA tranche).
2. **Steady state (Phase 7)**: every "I wish I'd noticed earlier" moment
   becomes a new entry. The miss is the historical_example.
3. **Removal**: a pattern that consistently produces false matches gets
   weight reduced, NOT removed — keep the audit trail.
4. **Bucket assignment**: each pattern belongs to one or more buckets.
   Multi-bucket allowed (e.g., `ira_negotiation_exposure` spans #4 + #11).

## Schema

Each entry in `catalog.yaml` is one warning-sign block:

```yaml
- id: snake_case_identifier
  name: "Human-readable name"
  buckets: [N, ...]              # one or more bucket IDs
  definition: |
    1-3 sentences. What the pattern is, what it predicts.
  keywords:                       # used by the heuristic offline scorer
    - "phrase 1"
    - "phrase 2"
  historical_example: |
    1-2 sentences. A real public event.
  invalidator: "One sentence — what would invalidate the match."
```

`id` is immutable once shipped — Phase 4 red-team outputs cite by id, and
old red-team rows in the DB reference these ids.

## Bumping the catalog version

`catalog_version` in `catalog.yaml` is part of the red-team idempotency key.
Bump it after any change so the pipeline re-runs the red team on every
score above T₂. Old red-team rows stay in the DB for audit.

## What the catalog is NOT

- It is not a checklist of items to test for. The red team consults it as
  a vocabulary; not every article needs to fit a pattern.
- It is not an alerting rulebook. Phase 5 alert routing is driven by
  composite score from Phase 3, not by warning-sign matches.
- It is not exhaustive. Phase 4 explicitly accepts catalog gaps — the red
  team can argue a short case even when no pattern fits.

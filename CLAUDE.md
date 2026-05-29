# Project rules for Claude Code

## What this project is

**SMA Monitor** is a thesis-drift monitoring system for a ~56-position
(mostly clinical/commercial biotech) equity book. It pulls the live portfolio
from IBKR Flex, ingests news + scientific/regulatory literature, scores each
item against a 12-bucket taxonomy (3 axes × 5 multipliers → composite + band),
red-teams above-threshold scores against a warning-sign catalog, and emits
alerts, an evening digest, and a per-position hold/watch/sell decision. A
FastAPI backend serves a React dashboard and a 9 AM ET thesis-drift email. A
daily ET cycle (collect 18:00 · dispatch 21:00 · thesis-email 09:00) drives the
whole thing; LLM work runs through the Codex CLI (no API key).

## Architecture (how files connect)

The Python package lives in `src/sma_monitor/`, run as
`python -m sma_monitor.<sub>`. The core is a linear analysis pipeline over a
shared SQLite DB (`data/sma.db`), wrapped by an orchestrator and three additive
feature layers (decision, API, frontend).

```
src/sma_monitor/
  config · paths · identity · db · logging_setup   PHASE 0  foundations (imported everywhere)
  portfolio/    PHASE 1  IBKR Flex pull ⨝ per-ticker YAML sidecar → Holding
  news/         PHASE 2  Brave/FMP/SEC/PubMed/CT.gov/S2 → articles + bucket tags
  scorer/       PHASE 3  3 axes × multipliers → composite score + threshold band
  red_team/     PHASE 4  above-T₂ scores → bearish thesis vs warning-sign catalog
  outputs/      PHASE 5  alerts (≥T) + evening digest + feedback marks
  orchestrator/ PHASE 6  drives the full cycle; cost budget, dead-letters, flags
  tuning/       PHASE 7  feedback → precision + weight/threshold suggestions
  decision/     W3       per-holding hold/watch/sell thesis-drift verdict (additive)
  llm/          W1/W9    Codex-CLI provider abstraction + throughput tiering
  api/          W5       FastAPI: /api/positions, thesis edit, file upload, status
frontend/       W6       Vite + React + TS + MUI dashboard → frontend/dist (served by api at /)
data/                    sma.db + YAML config (factor_buckets, warning_signs,
                         portfolio/sidecar) + generated artifacts (digests, scores, logs)
scripts/ · systemd/      cron + unit files for the daily ET schedule
```

Data flow: `portfolio → news → scorer → red_team → outputs`, with `tuning`
reading every phase's tables and `orchestrator` running them in sequence. The
`decision` engine is additive — it bundles a Phase 1 holding + Phase 3 scores +
Phase 4 red-team into a per-position verdict consumed by the API and the 9 AM
email.

Conventions: each subpackage follows the same shape — `__main__.py` (CLI),
`schema.py` (pydantic models), `store.py` (SQLite tables), and
`pipeline.py`/`engine.py` (logic), plus `*_client.py`/`prompt.py` where it talks
to an external service or LLM. Cross-phase joins use `identity.event_id`
(sha256 of canonical fields) for idempotency; every artifact also writes one row
to the universal `events` table. Re-runs are gated by versioning hinges
(`MULTIPLIERS_VERSION`, `catalog_version`, `DECISION_VERSION`). The full
data-flow diagram, module map, and all schemas live in `schema.md`.

## Comment style (applies to ALL code in this repo)

When writing or editing code in this project, add a leading comment
before every key code chunk explaining what it does. This overrides
Claude's default "minimal comments" preference for this repo only.

A "key code chunk" means:

- Each function or method definition (one short comment above the `def`)
- Each class definition (one short comment above the `class`)
- Each top-level constants block or configuration dict
- Each module-level schema string (e.g., `CREATE TABLE …` strings)

Do NOT add a comment before every line inside a function — only the
key chunks above. Comments should describe WHAT the block does in
plain English, not how Python works.

Keep comments concise (one short sentence is usually enough; two if
the block has a non-obvious reason to exist). Preserve existing
docstrings and module-level header comments.

Apply this to every file you touch — new code AND edits to existing
code.

## Reference docs

- `pl.md` — working plan + progress tracker (workstreams W1–W10)
- `schema.md` — architecture diagrams, module map, pydantic + SQL schemas

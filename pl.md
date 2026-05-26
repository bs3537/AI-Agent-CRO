# Plan — Thesis-Drift Dashboard + Morning Email on top of SMA Monitor

> Working plan saved for resumption. Status tracker is at the top; the full
> approved plan follows. Source of truth for design; update the tracker as
> workstreams land.

---

## ✅ Progress tracker (updated 2026-05-26)

| WS | Scope | Status |
|----|-------|--------|
| **W1** | LLM provider abstraction + Codex CLI client; refactor 3 Anthropic callsites; cost.py | **DONE (offline-verified)** |
| W3 | Thesis-drift decision engine + `position_decisions` table | pending |
| W4 | File upload + thesis editing (`portfolio/uploads.py`, `position_files`) | pending |
| W5 | FastAPI backend (`api/`) | pending |
| W6 | React + MUI dark/neon-orange frontend (`frontend/`) | pending |
| W2 | Brave (replaces Exa) + Scite + FMP sources | pending (needs API keys) |
| W7+W8 | 9 AM ET thesis-drift email + systemd timers + packaging | pending |

### W1 — what's already implemented and verified
- **New `src/sma_monitor/llm/`**: `provider.py` (`LLMProvider` protocol + `get_provider()`),
  `codex_client.py` (`CodexProvider`, `codex_available()`; runs
  `codex exec --output-schema <s> -o <out> --skip-git-repo-check --color never -`,
  prompt on stdin; `complete_json` / `complete_text`; env overrides `SMA_CODEX_BIN`,
  `SMA_CODEX_MODEL`, `CODEX_HOME`).
- **Refactored callsites** off the Anthropic SDK to the provider:
  `scorer/claude_client.py` (`score_with_llm` + `AXIS_SCHEMA`),
  `red_team/claude_client.py` (`red_team_with_llm` + `RED_TEAM_SCHEMA`, keeps catalog-id
  enrichment), `outputs/digest.py` (`_synthesize_narrative` → `provider.complete_text`).
  Back-compat aliases kept: `ClaudeError`/`RedTeamClaudeError` = `LLMError`,
  `DEFAULT_MODEL = "codex-cli"`.
- **Gating changed** from "api_key present" → "provider available" in
  `scorer/pipeline.py`, `scorer/calibration.py`, `red_team/pipeline.py`. `--offline`
  forces heuristic. `api_key` params retained (ignored) so orchestrator/`__main__` are untouched.
- **`orchestrator/cost.py`**: added `"codex-cli"` pricing (all-zero, subscription = no
  per-token USD) + `record_llm_call()` (zero-cost row so `status` shows Codex call counts).
- **Verified offline**: all 67 modules import; scorer/red-team offline → heuristic;
  non-offline with no Codex login → graceful heuristic fallback (no crash).
- **No remaining `import anthropic`** anywhere in `src/`.

### Next up (resume here)
1. ~~Finish W1 test~~ **DONE**: `tests/test_llm_provider.py` (7 passing) + `tests/stub_codex.py`.
   pytest installed via `uv pip install pytest`. Verified end-to-end: scorer non-offline with
   `SMA_CODEX_BIN=<shim>` labels rows `codex-cli` and records call counts in the cost ledger.
2. **W3 decision engine** (the dashboard's core) — see below. Build offline-first (heuristic
   verdict from red-team severity + composite band), then wire Codex via the W1 provider.
3. Then W4 → W5 → W6, then W2 (when keys arrive), then W7+W8.

### How to resume / test offline (no credentials needed)
Sandbox recipe (keeps live `data/sma.db` untouched): copy `data/` → `/tmp/sma_test_data`,
extract `raw_xml` from `position_pulls` into `portfolio/flex_fixture.xml`, delete the sandbox
`sma.db`, `export DATA_ROOT=/tmp/sma_test_data`, then:
```
python -m sma_monitor                                   # bootstrap (creates events table)
python -m sma_monitor.portfolio pull --from-file .../flex_fixture.xml
python -m sma_monitor.news poll --from-file .../news_cache/_sample_exa_response.json
python -m sma_monitor.scorer score --offline
python -m sma_monitor.red_team run --offline
python -m sma_monitor.orchestrator tick --offline --with-digest
```
NOTE: phase CLIs need the Phase 0 bootstrap (`python -m sma_monitor`) to have run first —
it's the only thing that creates the universal `events` table.

---

## Context

`sma_monitor` (phases 0–7, verified working) is a Python CLI/daily-batch agent: it
pulls IBKR positions, ingests news, scores per-article severity, red-teams, and emails
an evening digest. The user now wants a **web dashboard** as the primary surface:

- Every open IBKR position (via Flex Query) with **open gain/loss** and **portfolio weight**.
- Per position: an editable **thesis** box and **file upload** (thesis docs).
- A per-position **decision** that monitors *thesis drift* against everything ingested,
  shown as a color — 🟢 HOLD / 🟡 WATCH / 🔴 SELL — plus a **4–5 line note**.
- Frontend: professional, dark theme, **neon-orange** primary, Material Design.
- A daily **9:00 AM ET email** summarizing each position's thesis-drift decision.

Decisions confirmed with the user: **React + MUI + FastAPI**; LLM via **OpenAI Codex
subscription login** (no API key); deploy on an **always-on VM via systemd**; **Brave
replaces Exa**, with **Scite** (literature) and **FMP** (financials) added as enrichment.

This is additive — the existing phase 1–7 pipeline and tables stay; we add an API layer,
a decision engine, a provider swap, new sources, a frontend, and a morning email job.

---

## Architecture at a glance

```
React + MUI SPA (Vite, dark/neon-orange)
        │ REST + file upload + SSE/poll
        ▼
FastAPI backend  (src/sma_monitor/api/)  ── wraps existing sma_monitor functions
        │
        ├─ portfolio.joined.latest_joined()      positions ⨝ sidecar  (REUSE)
        ├─ scorer.store.recent_scores(ticker)     per-ticker evidence  (REUSE)
        ├─ red_team.store.recent_passes(ticker)   bearish evidence     (REUSE)
        ├─ portfolio.sidecar.write_sidecar()      thesis edits         (REUSE)
        ├─ decision/  NEW thesis-drift engine  → position_decisions table
        └─ llm/codex_client.py  DONE  → subprocess `codex exec --json --output-schema`

Ingestion (existing phase 2 pipeline) now feeds from:
  brave_client (replaces exa) · scite_client (#10) · fmp_client (#4/#7/#12)

Scheduler (systemd timers): 6 PM ET collect+decide · 9 AM ET thesis-drift email · 9 PM digest
```

---

## Workstream 1 — LLM provider swap to Codex (subscription login)  ✅ DONE

Anthropic SDK was imported directly in 3 places; there was no provider abstraction.

- **New `src/sma_monitor/llm/codex_client.py`**: `complete_json/complete_text` over
  `codex exec ... -` (prompt on stdin), `--output-schema` + `-o` for structured JSON.
  Auth = host ChatGPT login (`~/.codex/auth.json`); no API key.
- **New `src/sma_monitor/llm/provider.py`**: `LLMProvider` protocol + `get_provider()`
  → Codex when available, else None (heuristic fallback preserved).
- **Refactored** scorer/red-team/digest callsites to the provider, contracts unchanged.
- **`orchestrator/cost.py`**: `codex-cli` priced 0.0 + `record_llm_call()` (call counts).
- **Setup**: `codex login` (or `codex login --device-auth` on a headless VM) once on the host.

## Workstream 2 — Data sources: Brave replaces Exa; add Scite + FMP

Pipeline already swaps providers via a `Provider` callable alias (`news/pipeline.py:29`)
returning `list[ExaResult]`; only `_make_provider` is Exa-specific.

- **New `news/brave_client.py`**: same `search(query, *, api_key, num_results,
  start/end_published_date) -> list[ExaResult]` shape; point `_make_provider` at Brave.
- **New `news/scite_client.py`**: literature for held tickers' drugs/indications → bucket #10.
- **New `news/fmp_client.py`**: FMP fundamentals/ratios/news → decision engine (#4/#7/#12)
  + dashboard financial fields. (Scite/FMP MCP tools in the dev session are research-only;
  the app calls their REST APIs with the user's keys.)
- **`news/source_tiers.py`**: add brave/scite/fmp hosts.
- **`config.py`**: add `brave_search_api_key`, `scite_api_key`, `fmp_api_key`; `missing_for(2)`
  → require `brave_search_api_key` instead of `exa_api_key`.

## Workstream 3 — Thesis-drift decision engine (NEW phase, the dashboard's core)

No per-position rollup exists today. Build a dedicated per-position synthesis.

- **New module `src/sma_monitor/decision/`**:
  - `schema.py`: `PositionDecision{ ticker, verdict: Literal["hold","watch","sell"], color,
    note: str (4–5 lines), drivers: list[str], confidence, thesis_hash, inputs_hash,
    model_used, decided_at }`.
  - `prompt.py`: "thesis-drift monitor" system prompt; watch-item voice (reuse constraints
    from `red_team/prompt.py`). User msg bundles thesis + extracted file text +
    `recent_scores(ticker)` + `recent_passes(ticker)` + FMP metrics + catalysts + open P&L.
  - `engine.py`: builds the bundle, calls the Codex provider with an `--output-schema`
    matching `PositionDecision`; heuristic fallback from max red-team severity + composite band.
  - `store.py`: `position_decisions` table (latest-per-ticker view); registers into `events`.
- **Color**: hold→green, watch→yellow, sell→red; red-team `severity_of_concern ≥ 4` forces ≥ watch.

## Workstream 4 — File upload + thesis editing

- **Thesis edit**: reuse `portfolio/sidecar.py:write_sidecar()` (load → set `.thesis` → write);
  create a minimal sidecar if none exists.
- **New `portfolio/uploads.py`**: save under `data/portfolio/uploads/{TICKER}/`, record in a
  new `position_files` table, extract text (`pypdf` + `python-docx`) to a cached `.txt`.

## Workstream 5 — FastAPI backend

- **New `src/sma_monitor/api/app.py`** + `routes/`:
  - `GET /api/positions` → holdings + `open_pnl = market_value - cost_basis`, `pnl_pct`,
    `pct_nav`, nearest catalyst, latest decision. From `latest_joined()` + decision store.
  - `GET /api/positions/{ticker}` → detail (scores, red-team, files, financials).
  - `PUT /api/positions/{ticker}/thesis` → `write_sidecar()`.
  - `POST /api/positions/{ticker}/files` (multipart) → `uploads.py`.
  - `POST /api/positions/{ticker}/recompute` → decision engine (background task).
  - `GET /api/status` → reuse orchestrator status.
- uvicorn; serve the built React bundle as static files in production.

## Workstream 6 — React + MUI frontend

- **New `frontend/`** (Vite + React + TS + MUI). Dark theme via `createTheme`
  (`mode:'dark'`, `primary.main` = neon orange e.g. `#FF6A00`).
- Positions grid: ticker · open P&L (green/red ▲▼) · % NAV · inline thesis editor
  (autosave → PUT) · upload button (→ POST) · colored decision chip + 4–5 line note. Detail
  drawer (scored articles, bearish theses, financials, catalysts). Per-row "Recompute".
- Poll `/api/positions` (or SSE) so decisions refresh after batch runs / recompute.

## Workstream 7 — Morning 9 AM ET thesis-drift email + scheduling

- **New `outputs/thesis_email.py`**: email listing each position with color, verdict, P&L,
  %NAV, 4–5 line note, ordered sell→watch→hold then %NAV. Reuse `outputs/channels.py`
  (EmailChannel + FileChannel archive).
- **`orchestrator/`**: `run_morning_thesis_cycle()` (recompute stale decisions → send) +
  `thesis-email` CLI subcommand.
- **`orchestrator/schedule.py`** (ET-aware) + `systemd/`: 09:00 ET thesis email, keep 18:00 ET
  collect (+decide) and 21:00 ET digest. ("9 AM EST" = `America/New_York`, DST-aware.)

## Workstream 8 — Packaging / runtime

- `pyproject.toml`: add `fastapi`, `uvicorn[standard]`, `python-multipart`, `pypdf`,
  `python-docx`. Frontend deps in `frontend/package.json`.
- `systemd/`: units for the API server + timers; document `codex login` on the host.
- Remove `anthropic` once migrated (DONE in code; can drop from pyproject) or retain as
  optional API-key fallback.

---

## Critical files

**Create:** `src/sma_monitor/llm/{codex_client,provider}.py` ✅,
`src/sma_monitor/news/{brave_client,scite_client,fmp_client}.py`,
`src/sma_monitor/decision/{schema,prompt,engine,store}.py`,
`src/sma_monitor/portfolio/uploads.py`,
`src/sma_monitor/api/{app,routes/*}.py`,
`src/sma_monitor/outputs/thesis_email.py`, `frontend/**`, new `systemd/*` units.

**Modify:** `scorer/claude_client.py` ✅, `red_team/claude_client.py` ✅, `outputs/digest.py` ✅
(→ Codex provider); `news/pipeline.py` (`_make_provider`→Brave); `news/source_tiers.py`;
`config.py` (new keys, `missing_for`); `orchestrator/{cost ✅,schedule,__main__,pipeline}.py`;
`pyproject.toml`; `.env.example`; `README`/`schema.md`/`PLAN.MD` (new phase + decision table).

**Reuse (no change):** `portfolio/joined.py:latest_joined`, `portfolio/sidecar.py:write_sidecar`,
`scorer/store.py:recent_scores`, `red_team/store.py:recent_passes`,
`outputs/channels.py:EmailChannel`, `db.py:connection/init_db`, `identity.py:event_id`.

---

## Build order (incremental, demoable before credentials arrive)

1. **LLM provider + Codex client** (W1) ✅ — heuristic fallback keeps everything runnable.
2. **Decision engine + `position_decisions`** (W3) — offline heuristic verdict.
3. **FastAPI backend** (W5) + thesis edit + upload (W4).
4. **React/MUI frontend** (W6) against the API using sandbox/fixture data.
5. **Brave/Scite/FMP** (W2) once keys exist; swap Exa→Brave.
6. **Morning email + systemd timers** (W7/W8).

Whole stack stays runnable **offline** (heuristic decisions + news fixtures + no Codex login)
throughout, mirroring the existing `--offline` design.

---

## Verification

- **Provider/Codex:** stub `codex` on PATH (SMA_CODEX_BIN) emitting canned schema-conforming
  JSON; assert parse + heuristic fallback when absent. *(in progress)*
- **Decision engine offline:** seed the sandbox DB, run engine heuristically, assert a row per
  holding with valid verdict/color/note and sev ≥4 forcing ≥ watch.
- **API:** uvicorn up; `GET /api/positions` returns P&L + %NAV + decision; `PUT .../thesis`
  round-trips to YAML; `POST .../files` stores + extracts; `recompute` updates the decision.
- **Frontend:** `npm run dev`, edit a thesis, upload a PDF, recompute, confirm chip + note update.
- **Email:** `python -m sma_monitor.orchestrator thesis-email` writes the archived file and
  (with SMTP) sends; ordering sell→watch→hold.
- **Schedule:** `orchestrator install-cron` / systemd timers show a 09:00 ET thesis-email entry.
- **End-to-end live:** after `codex login` + Brave/Scite/FMP/IBKR keys, run a collect cycle and
  confirm decisions reflect ingested evidence; the 9 AM email matches the dashboard.

## Open items to confirm during build (not blocking)

- Morning 9 AM email is **in addition to** the evening digest (kept as-is) — say if it should
  **replace** it instead.
- Exact neon-orange hex + logo/branding for the MUI theme.
- Whether to fully remove `anthropic` from pyproject or retain it as an API-key fallback.

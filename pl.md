# Plan — Thesis-Drift Dashboard + Morning Email on top of SMA Monitor

> Working plan saved for resumption. Status tracker is at the top; the full
> approved plan follows. Source of truth for design; update the tracker as
> workstreams land.

---

## ✅ Progress tracker (updated 2026-05-27)

| WS | Scope | Status |
|----|-------|--------|
| **W1** | LLM provider abstraction + Codex CLI client; refactor 3 Anthropic callsites; cost.py | **DONE (offline-verified)** |
| **W3** | Thesis-drift decision engine + `position_decisions` table | **DONE (offline-verified)** |
| **W4** | File upload + thesis editing (`portfolio/uploads.py`, `position_files`) | **DONE (offline-verified)** |
| **W5** | FastAPI backend (`api/`) | **DONE (offline-verified)** |
| **W6** | React + MUI dark/neon-orange frontend (`frontend/`) | **DONE (npm build verified)** |
| **W2** | Brave (replaces Exa) + Scite + FMP sources | **SCAFFOLDED (offline-verified; add keys to go live)** |
| **W7** | 9 AM ET thesis-drift email + scheduling | **DONE (offline-verified)** |
| **W8** | systemd units (API + timers) + packaging/codex-login docs | **DONE (units validated)** |

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

### W3 — what's already implemented and verified
- **New `src/sma_monitor/decision/`**: `schema.py` (`PositionDecision`, `DecisionCandidate`,
  `ScoreEvidence`/`BearEvidence`, `VERDICT_COLOR`), `prompt.py` (thesis-drift monitor system +
  user message; verdict framed as a thesis-integrity signal, note in neutral watch-item voice),
  `engine.py` (`build_candidate` from `latest_joined` + `recent_scores` + `recent_passes`;
  heuristic verdict from `max_severity` + composite band vs `T`/`T2`; Codex path via the W1
  provider under `DECISION_OUTPUT_SCHEMA`; **sev ≥ 4 forces ≥ watch** guard; `thesis_hash` +
  `inputs_hash` gate re-compute; `run_decisions` skips unchanged holdings), `store.py`
  (`position_decisions` table, `latest_decisions()` latest-per-ticker, `has_decision_for`).
- **CLI**: `python -m sma_monitor.decision recompute [--ticker T] [--offline] [--limit N] [--force]`
  and `… show [--ticker T]`. Bootstrap (`python -m sma_monitor`) now calls `init_decision_schema`.
- **Forward-compat slots**: `thesis_doc_text` (W4 uploads) and `fmp_metrics` (W2) live on the
  candidate already, so wiring them later needs no schema change.
- **Cost ledger**: LLM path records a zero-cost `kind="decision"` row via `record_llm_call`.
- **Tests**: `tests/test_decision_engine.py` (9 passing) — heuristic verdict/color, sev≥4 guard,
  LLM-path parsing, bad-verdict→watch, inputs_hash staleness (evidence + thesis edits). Full
  suite green (16 passing).
- **Offline-verified end-to-end** via the sandbox recipe: bootstrap → pull → news poll → scorer
  `--offline` → red_team `--offline` → `decision recompute --offline` writes one row per holding
  (3/3), `show` renders verdict+note, re-run is idempotent (decided=0, skipped=3).

### W4 — what's already implemented and verified
- **New `src/sma_monitor/portfolio/uploads.py`**: `position_files` table + `init_uploads_schema`;
  `save_upload(ticker, filename, bytes)` stores under `data/portfolio/uploads/{TICKER}/`
  (content-prefixed name), extracts + caches text to a `.txt` sidecar, and records the row
  (idempotent on `sha256(content)` via `UNIQUE(ticker, content_sha)`); `extract_text` (.txt/.md
  direct, .pdf via `pypdf`, .docx via `python-docx`, both lazy-imported → `UploadError` with
  install hint if absent); `list_files`, `read_text`, `combined_text(ticker)` (labeled,
  truncated to 8k chars → the decision engine), `delete_file`, `save_upload_from_path`.
- **Thesis editing**: `portfolio/sidecar.py:set_thesis(ticker, thesis)` — load → set `.thesis` →
  `write_sidecar`; creates a minimal sidecar (neutral tier 3 / hybrid stage) if none exists.
- **Engine wiring**: `decision/engine.build_candidate` now fills `thesis_doc_text=combined_text()`,
  and `inputs_hash` folds in a `doc_hash` so a thesis edit OR a doc upload re-computes that
  holding (verified: edit+upload → `decided=1` for the touched ticker, `skipped=2` for the rest).
- **CLI** (`python -m sma_monitor.portfolio`): `set-thesis --ticker T (--thesis | --from-file)`,
  `add-file --ticker T --file PATH`, `list-files [--ticker T]`. Bootstrap calls
  `init_uploads_schema`; `paths.UPLOADS_DIR` added to `ALL_DIRS`.
- **Deps**: `pypdf>=4.0` + `python-docx>=1.1` added to `pyproject.toml` and installed.
- **Tests**: `tests/test_uploads.py` (6 passing) — txt/md/docx extraction, unsupported→error,
  content-addressed id. Full suite green (22 passing).

### W5 — what's already implemented and verified
- **New `src/sma_monitor/api/`**: `app.py` (`create_app()` factory + module-level `app`; CORS for
  the Vite dev origins via `SMA_API_CORS_ORIGINS`; lifespan does `ensure_dirs` + `init_db`; mounts
  the built SPA from `frontend/dist` when present), `__main__.py` (`python -m sma_monitor.api
  [--host --port --reload]` → uvicorn), `schemas.py` (wire models), `routes/positions.py`,
  `routes/status.py`. `__init__.py` exports only `create_app` (re-exporting `app` would shadow the
  `app` submodule).
- **Endpoints** (all `/api`): `GET /health`; `GET /positions` (grid: `open_pnl =
  market_value − cost_basis`, `pnl_pct`, `%NAV`, nearest catalyst, latest decision, file count,
  plus `missing_sidecars`); `GET /positions/{ticker}` (detail: scored articles, red-team passes,
  uploaded files, catalysts; `financials` null until W2); `PUT /positions/{ticker}/thesis`
  (→ `sidecar.set_thesis`, creates a minimal sidecar if none); `POST /positions/{ticker}/files`
  (multipart → `uploads.save_upload`; 415 on unsupported type); `DELETE
  /positions/{ticker}/files/{event_id}`; `POST /positions/{ticker}/recompute` (background by
  default, `?wait=true&offline=…` runs inline and returns the decision); `GET /status` (orchestrator
  spend/degrade/flags/dead-letters + position count). 404 for non-held tickers.
- **Deps**: `fastapi` + `uvicorn[standard]` + `python-multipart` added to `pyproject` and installed.
- **Test isolation**: new `tests/conftest.py` redirects `DATA_ROOT` to a session copy of `data/`
  so the whole suite is hermetic (no test touches the live DB/sidecars/uploads) while API tests get
  realistic seed state.
- **Tests**: `tests/test_api.py` (9, TestClient) — health, grid+P&L, detail, 404, thesis round-trip,
  upload + extract, unsupported-type 415, synchronous recompute, status. Full suite green
  (31 passing). Also smoke-tested the real uvicorn server over HTTP (health/positions/recompute/status).

### W6 — what's already implemented and verified
- **New `frontend/`** (Vite + React 18 + TS + MUI 5): `package.json`, `vite.config.ts` (dev proxy
  `/api`→`:8000`), `tsconfig(.node).json`, `index.html`, `.gitignore`, `README.md`.
- **`src/`**: `theme.ts` (dark, primary `#FF6A00`; verdict→hex map), `types.ts` (mirrors the W5
  schemas), `api.ts` (typed fetch client), `main.tsx`, `App.tsx` (loads + 30s-polls
  `/positions`+`/status`; wires thesis-save / upload / recompute, refreshing after each).
- **Components**: `PositionCard` (P&L ▲▼, %NAV, catalyst, decision chip + note + driver chips,
  inline thesis editor, upload, recompute, details), `DecisionChip`, `PnL`, `ThesisEditor`
  (debounced autosave → PUT), `FileUpload`, `DetailDrawer` (scores / red-team / files-deletable /
  catalysts), `StatusBar` (spend + position count).
- **Toolchain**: Node not present in the dev env → installed Node 20 LTS into `~/.local/node`
  (no sudo). `npm install` + `npm run build` (tsc typecheck + vite) both pass; `dist/` emitted.
- **Integration verified**: with `frontend/dist` present, the W5 backend serves the SPA at `/`
  (200, index.html) while `/api/*` still routes (API mounts take precedence over the static catch-
  all). Python suite still green (31 passing).

### W7 — what's already implemented and verified
- **New `outputs/thesis_email.py`**: `assemble_thesis_email()` joins `latest_decisions()` ×
  `latest_joined()` (open P&L = market_value − cost_basis, %NAV), orders sell→watch→hold then
  %NAV desc, renders per-position markdown (color glyph, verdict, P&L, catalyst, 4–5 line note,
  drivers, confidence) and sends via channels. `render_thesis_email_markdown()` is the renderer.
- **`outputs/channels.py`**: added `send_thesis_email` (concrete no-op default on the base →
  optional capability; abstract contract stays alert+digest). FileChannel archives to
  `data/digests/thesis/YYYY-MM-DD.md` (separate from the evening digest), EmailChannel sends via
  SMTP, StdoutChannel prints.
- **Orchestrator**: `run_collect_cycle` now `+decide` (runs `run_decisions` after red-team so the
  morning verdicts reflect the day's evidence); new `run_morning_thesis_cycle` (recompute stale →
  send); `thesis-email` CLI subcommand.
- **Scheduler** (`schedule.py`): added the **9 AM ET** firing (`MORNING_TIME_ET`) to the run_loop
  (now 3 firings: 9 AM thesis / 6 PM collect / 9 PM dispatch) and the crontab generator.
  **Additive** — the evening digest at 9 PM is unchanged. (To make it *replace* the digest instead,
  drop the dispatch firing.)
- **Tests**: `tests/test_thesis_email.py` (4) — render basics, caller-sort ordering, missing-P&L,
  and an assemble round-trip (seed decisions offline → capture channel). Full suite 35 passing.
- **Offline-verified**: `orchestrator thesis-email --offline` archives the ordered email to
  `digests/thesis/`; `install-cron` shows the 9 AM line.

### W8 — what's already implemented and verified
- **New `systemd/` units**: `sma-api.service` (always-on uvicorn + dashboard, bound to localhost);
  oneshot service+timer pairs `sma-thesis-email` (09:00 ET), `sma-collect` (18:00 ET),
  `sma-dispatch` (21:00 ET) using `OnCalendar=… America/New_York` (systemd ≥ 252) with
  `Persistent=true`. Updated `sma-monitor.service` (run-loop) header — it's the version-independent
  ALTERNATIVE to the timers (enable one or the other, not both).
- **`systemd/README.md`**: host setup (dedicated `sma` user, venv, `pip install -e`), `.env`,
  **`codex login`** as the LLM auth (no API key), `npm run build` for the dashboard, install/operate
  commands, and the localhost-binding security note (no API auth → SSH tunnel / reverse proxy).
- **Packaging**: dropped the now-unused `anthropic` dep from `pyproject` (no `import anthropic`
  remains anywhere). `.env.example` rewritten to document Codex-login auth + optional
  `SMA_API_CORS_ORIGINS` / `SMA_FRONTEND_DIST` / `SMA_CODEX_*`.
- **Verified**: `systemd-analyze` parses every unit (only flags the prod `/opt/...` python path,
  expected here); `systemd-analyze calendar` confirms the three ET expressions resolve DST-aware
  (09:00 EDT → 13:00 UTC, etc.); `install-cron` lists all three firings; full suite 35 passing after
  the anthropic drop.

### W2 — scaffolded (plug-and-play; add keys to `.env` to go live)
- **New `news/brave_client.py`**: Brave News Search REST → `ExaResult` (same shape), freshness
  range from the poll window, `load_response_file` for replay. `pipeline._make_provider` now prefers
  **Brave > Exa > fixture**, clear `RuntimeError` otherwise.
- **New `news/scite_client.py`** + `pipeline.poll_literature()`: Scite literature → `ExaResult`
  with canonical `https://doi.org/{doi}` links (tier-3), forced into **bucket #10**. `query.literature_query()`
  builds the drug/indication term. *(Endpoint/field names isolated in `SCITE_SEARCH`/`_parse_response`
  with a CONFIRM-against-docs note — the one spot to adjust when the key lands.)*
- **New `news/fmp_client.py`**: profile + TTM ratios + TTM key-metrics → flat metrics dict;
  `fmp_snapshots` table (one row/ticker/day); `refresh_for_holdings()` (live or `{ticker: metrics}`
  fixture); `latest_fmp_metrics()`. Wired into `decision.build_candidate` (`fmp_metrics` + folded into
  `inputs_hash`) and the API detail's `financials`.
- **Config / tiers / CLIs / schedule**: `brave_search_api_key`/`scite_api_key`/`fmp_api_key` +
  `missing_for(2)`→brave; `doi.org`/PubMed → tier 3; `news poll-literature` + `news fmp` subcommands;
  `collect` cycle now runs literature + financials (guarded — `scite_failure`/`fmp_failure` flags,
  skipped without keys). Bootstrap inits `fmp_snapshots`. `.env.example` documents the three keys.
- **Offline-verified**: `tests/test_w2_sources.py` (8) — fixture parsers, missing-key guards,
  FMP snapshot round-trip, FMP→candidate pickup; full suite 43 passing. Sandbox: literature fixture →
  3 bucket-#10 tags, FMP fixture → 2 snapshots flowing into decisions; `collect --offline` exits 0
  with literature/financials skipped gracefully.

### Post-plan additions (offline-verified)
- **Detail-drawer financials**: the drawer now renders a "Financials (FMP)" section
  (`DetailDrawer.tsx`) from the API's `financials` field — closes the W6 spec gap.
- **Per-position sparkline**: `fmp_client` fetches/stores ~1yr daily EOD closes (`price_series`
  table, `fetch_price_history`/`save_price_series`/`latest_price_series`,
  `refresh_prices_for_holdings`); the API serves them per grid row as `spark`; the frontend renders
  a dependency-free SVG `Sparkline` (price line only, green/red by net change) at the front of each
  card. `news fmp` + the collect cycle refresh prices alongside metrics (FMP-key-gated; fixture
  `_sample_fmp_prices.json` for offline). Tests in `tests/test_w2_sources.py`; full suite 46 passing.

### Remaining to go live (no code — just credentials + one verification)
1. Put `BRAVE_SEARCH_API_KEY`, `SCITE_API_KEY`, `FMP_API_KEY` in `.env`.
2. Confirm the Scite search endpoint/fields against current Scite API docs (the one CONFIRM marker in
   `scite_client.py`); Brave/FMP use documented endpoints.
3. Run a live `collect` and spot-check ingested Brave articles, bucket-#10 literature, and FMP
   snapshots; the dashboard `financials` + decision notes should then reflect real data.

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

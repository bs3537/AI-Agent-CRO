# SMA Monitor — Schema & Architecture Reference

This document is the structural map of the project. It captures:

1. The data-flow diagram (how phases connect)
2. The module map (what each file does)
3. Cross-phase import dependencies
4. Pydantic model schemas (in-memory data shapes)
5. SQLite table schemas (on-disk persistence)
6. Versioning hinges (`event_id`, `MULTIPLIERS_VERSION`, `catalog_version`)

---

## 1. Data flow

```
                          DATA FLOW (top to bottom)
                          ─────────────────────────

  ┌─────────┐  pull   ┌──────────────┐  query   ┌──────────────┐
  │  IBKR   │ ──────► │   PHASE 1    │ ───────► │   PHASE 2    │
  │  Flex   │         │  portfolio   │          │     news     │
  └─────────┘         │  Position ⨝  │          │   Exa search │
                      │  Sidecar →   │          │  +  bucket   │
                      │  Holding     │          │   tagging    │
                      └──────────────┘          └──────────────┘
                              │                         │
                              │ Holding                 │ Article + tags
                              └────────────┬────────────┘
                                           ▼
                              ┌─────────────────────────┐
                              │       PHASE 3           │
                              │       scorer            │
                              │  3 axes × 5 mults       │
                              │  → composite + band     │
                              └─────────────────────────┘
                                           │
                                           │ composite ≥ T₂
                                           ▼
                              ┌─────────────────────────┐
                              │       PHASE 4           │
                              │      red team           │
                              │  cite warning signs     │
                              └─────────────────────────┘
                                           │
                                           ▼
                              ┌─────────────────────────┐
                              │       PHASE 5           │
                              │       outputs           │
                              │  alerts (≥T) + digest   │
                              │  + feedback marks       │
                              └─────────────────────────┘
                                           │
                                           │ event_ids + feedback
                                           ▼
                              ┌─────────────────────────┐
                              │       PHASE 7           │
                              │     tuning loop         │
                              │  precision → weight     │
                              │     suggestions         │
                              └─────────────────────────┘

       ┌──────────────────────────────────────────────────────────┐
       │  PHASE 6 — orchestrator drives the whole cycle           │
       │  positions → news → score → red-team → alerts → digest   │
       └──────────────────────────────────────────────────────────┘

       ┌──────────────────────────────────────────────────────────┐
       │  PHASE 0 — Foundations imported by every phase           │
       │  config · paths · identity · db · logging_setup          │
       └──────────────────────────────────────────────────────────┘
```

---

## 2. Module map

```
┌─ PHASE 0 — Foundations (imported by every phase) ────────────────┐
│  __main__.py        bootstrap smoke test (creates dirs, schemas) │
│  config.py          .env loader (pydantic-settings)              │
│  paths.py           data directory constants                     │
│  identity.py        event_id = sha256(canonical fields)          │
│  db.py              SQLite connection + universal events table   │
│  logging_setup.py   structured JSON logs to stdout               │
└──────────────────────────────────────────────────────────────────┘

┌─ PHASE 1 — Portfolio ────────────────────────────────────────────┐
│  __main__.py     CLI: pull, show, show-joined, validate-sidecar  │
│  flex.py         IBKR Flex Web Service client + XML parser       │
│  schema.py       Position, Sidecar, Holding, Catalyst models     │
│  sidecar.py      load/write per-ticker YAML + set_thesis (W4)    │
│  joined.py       Position ⨝ Sidecar → Holding (canonical input)  │
│  uploads.py      W4: thesis-doc upload + extract → position_files│
│  store.py        position_pulls + positions tables               │
└──────────────────────────────────────────────────────────────────┘

┌─ PHASE 2 — News ─────────────────────────────────────────────────┐
│  __main__.py     CLI: poll, show, coverage, show-buckets/queries │
│  buckets.py      load 12-bucket taxonomy YAML                    │
│  query.py        build per-holding / sector query strings        │
│  exa_client.py   Exa /search adapter + fixture loader            │
│  tagger.py       deterministic keyword → bucket tagger           │
│  source_tiers.py URL host → priority tier (1=SEC … 6=retail)     │
│  pipeline.py     poll loop, dedup, persistence                   │
│  store.py        articles + article_tickers/buckets + polls      │
└──────────────────────────────────────────────────────────────────┘

┌─ PHASE 3 — Scorer ───────────────────────────────────────────────┐
│  __main__.py     CLI: score, show, calibrate, show-multipliers   │
│  schema.py       AxisScores, CompositeScore, ScoreCandidate      │
│  rubric.py       3-axis rubric text (cached in system prompt)    │
│  multipliers.py  bucket/conviction/position/catalyst weights +   │
│                  T/T₂ thresholds (bump VERSION → re-score)       │
│  prompt.py       Claude system + user message builders           │
│  heuristic.py    deterministic offline scorer fallback           │
│  claude_client.py  Anthropic SDK wrapper (Sonnet 4.6)            │
│  pipeline.py     score_unscored → compose → save                 │
│  store.py        scores table (UNIQUE per multipliers_version)   │
│  calibration.py  historical-event check harness                  │
└──────────────────────────────────────────────────────────────────┘

┌─ PHASE 4 — Red team ─────────────────────────────────────────────┐
│  __main__.py     CLI: run, show, show-catalog, library-coverage  │
│  schema.py       RedTeamCandidate, RedTeamResult, MatchedWS      │
│  catalog.py      load warning_signs/catalog.yaml                 │
│  prompt.py       voice-constrained system prompt + user msg     │
│  heuristic.py    keyword-overlap offline red team                │
│  claude_client.py  Anthropic wrapper + catalog-id validation     │
│  pipeline.py     pick above-T₂ → run → save                      │
│  store.py        red_team_passes table                           │
└──────────────────────────────────────────────────────────────────┘

┌─ PHASE 5 — Outputs ──────────────────────────────────────────────┐
│  __main__.py     CLI: alerts, digest, mark, mark-missed          │
│  schema.py       AlertRecord, DigestEvent, DigestSummary         │
│  alerts.py       composite ≥ T → suppression → dispatch          │
│  digest.py       evening digest assembler (~4:15pm ET)           │
│  channels.py     File / Stdout / Email (+ send_thesis_email, W7) │
│  format.py       render alert text + digest markdown             │
│  thesis_email.py W7: morning thesis-drift email (per position)   │
│  feedback.py     mark useful/noise + mark-missed                 │
│  store.py        alerts, digests, feedback, missed_events        │
└──────────────────────────────────────────────────────────────────┘

┌─ PHASE 6 — Orchestrator ─────────────────────────────────────────┐
│  __main__.py     CLI: collect, dispatch, thesis-email, tick,     │
│                  run, status, install-cron, retry-DL             │
│  pipeline.py     collect (+decide) · dispatch · morning-thesis   │
│  schedule.py     3 ET firings (9AM/6PM/9PM) loop + crontab gen   │
│  cost.py         pricing, daily budget, degrade cascade          │
│  dead_letter.py  retry-once-then-abandon policy                  │
│  flags.py        named operational flags (stale_positions, …)    │
│  store.py        cost_ledger, system_flags, dead_letters         │
└──────────────────────────────────────────────────────────────────┘

┌─ PHASE 7 — Tuning ───────────────────────────────────────────────┐
│  __main__.py        CLI: report, precision, library-candidates   │
│  precision.py       per-bucket alert precision from feedback     │
│  weights.py         bucket-weight + threshold suggestions        │
│  library_growth.py  missed events → catalog YAML drafts          │
│  conviction_review.py  tier distribution + stale-sidecar check   │
│  bucket_review.py   PLAN §7 architectural questions              │
│  report.py          assemble markdown to data/tuning/YYYY-MM-DD  │
└──────────────────────────────────────────────────────────────────┘

┌─ WORKSTREAM 3 — Decision (thesis-drift, additive) ───────────────┐
│  __main__.py     CLI: recompute, show                            │
│  schema.py       PositionDecision, DecisionCandidate, *Evidence  │
│  prompt.py       thesis-drift monitor system + user message      │
│  engine.py       bundle → heuristic / Codex verdict → persist    │
│  store.py        position_decisions table (latest-per-ticker)    │
└──────────────────────────────────────────────────────────────────┘

┌─ WORKSTREAM 5 — API (FastAPI, additive) ─────────────────────────┐
│  __main__.py        uvicorn entrypoint (host/port/reload)        │
│  app.py             create_app(): CORS, lifespan, static SPA     │
│  schemas.py         PositionSummary/Detail, DecisionOut, FileOut │
│  routes/positions.py  GET grid+detail, PUT thesis, POST files/   │
│                       recompute, DELETE file                     │
│  routes/status.py   GET /api/status (orchestrator snapshot)      │
└──────────────────────────────────────────────────────────────────┘
```

API endpoints (all under `/api`): `GET /health`, `GET /positions`,
`GET /positions/{ticker}`, `PUT /positions/{ticker}/thesis`,
`POST /positions/{ticker}/files`, `DELETE /positions/{ticker}/files/{event_id}`,
`POST /positions/{ticker}/recompute` (bg; `?wait=true` runs inline), `GET /status`.

WORKSTREAM 6 — `frontend/` (Vite + React + TS + MUI 5, outside the Python tree):
dark theme / neon-orange `#FF6A00`; positions grid with P&L, decision chip +
note, inline thesis autosave, file upload, per-row recompute, and a detail
drawer. `npm run build` → `frontend/dist`, which the W5 backend serves at `/`.

---

## 3. Cross-phase imports

| Phase | Reads from |
|---|---|
| 1 portfolio | Phase 0 only |
| 2 news | Phase 0 + Phase 1 (`portfolio.joined.latest_joined`) |
| 3 scorer | Phase 0 + Phase 1 + Phase 2 (`news.buckets.load_buckets`) |
| 4 red team | Phase 0 + Phase 1 + Phase 2 + Phase 3 (`scorer.multipliers.T2`) |
| 5 outputs | All earlier phases (joins scores ⨝ red_team_passes) |
| 6 orchestrator | All other phases — drives them |
| 7 tuning | Reads every phase's tables; writes only to `data/tuning/` |
| W3 decision | Phase 1 (`joined.latest_joined`, `uploads.combined_text`) + Phase 3 (`scorer.store.recent_scores`, `multipliers.T/T2`) + Phase 4 (`red_team.store.recent_passes`) + `llm.get_provider` |
| W4 uploads | Phase 0 (`db`, `identity`, `paths`) + Phase 1 (`portfolio.sidecar`); optional `pypdf` / `python-docx` extractors |
| W5 api | Phase 1 (`joined`, `sidecar`, `uploads`) + Phase 3/4 stores + W3 decision (`run_decisions`, `latest_decision`) + Phase 6 (`status`); `fastapi` / `uvicorn` |

---

## 4. Pydantic schemas (in-memory data shapes)

### Phase 1 — Portfolio (`portfolio/schema.py`)

```python
Stage = Literal["clinical_stage", "commercial_stage", "hybrid"]
ConvictionTier = Literal[1, 2, 3, 4, 5]
CatalystType = Literal["clinical", "regulatory", "commercial", "corporate", "other"]
Confidence = Literal["high", "medium", "low"]

class Position:
    ticker: str
    qty: float
    market_value: float
    pct_nav: float
    cost_basis: float | None
    pulled_at: datetime
    nav: float

class Catalyst:
    date: date
    type: CatalystType
    description: str
    confidence: Confidence = "medium"
    resolved: bool = False
    resolution_note: str | None = None

class Sidecar:
    ticker: str
    conviction_tier: ConvictionTier
    stage: Stage
    thesis: str
    company_name: str | None
    aliases: list[str]
    brands: list[str]
    products: list[str]
    indications: list[str]
    catalysts: list[Catalyst]

class Holding:                       # Position ⨝ Sidecar
    # all Position fields, all Sidecar fields, plus:
    nearest_catalyst_days: int | None
    has_overdue_catalyst: bool
```

### Phase 2 — News (`news/buckets.py`)

```python
BuildTier = Literal["A", "B", "C", "D"]
BucketScope = Literal["per_holding", "sector"]

class Bucket:
    id: int                          # 1..12
    name: str
    short_name: str
    build_tier: BuildTier
    scope: BucketScope
    description: str
    search_terms: list[str]

class Taxonomy:
    version: int
    buckets: list[Bucket]

class BucketTag:                     # tagger.py
    bucket_id: int
    confidence: float
```

### Phase 3 — Scorer (`scorer/schema.py`)

```python
ThresholdBand = Literal["above_t", "t2_to_t", "below_t2"]

class AxisScores:                    # LLM/heuristic output
    financial_impact: float          # 0..10
    narrative_shift: float           # 0..10
    time_criticality: float          # 0..10
    rationale: str
    confidence: float                # 0..1

class ScoreCandidate:                # input bundle
    article_event_id: str
    title: str
    excerpt: str
    source: str | None
    source_tier: int | None
    published_at: datetime | None
    ticker: str
    pct_nav: float
    conviction_tier: int
    stage: str
    thesis: str
    nearest_catalyst_days: int | None
    primary_bucket_id: int
    primary_bucket_name: str
    primary_bucket_confidence: float
    secondary_buckets: list[tuple[int, float]]

class CompositeScore:                # resolved row, persisted to scores table
    article_event_id: str
    ticker: str
    primary_bucket_id: int
    secondary_buckets: list[tuple[int, float]]
    financial_impact: float
    narrative_shift: float
    time_criticality: float
    raw_avg: float
    bucket_weight: float
    position_weight: float
    conviction_mult: float
    catalyst_boost: float
    stage_interaction: float
    composite: float
    threshold_band: ThresholdBand
    rationale: str
    confidence: float
    model_used: str
    multipliers_version: str
    inputs_hash: str
    scored_at: datetime
```

### Phase 4 — Red team (`red_team/{schema,catalog}.py`)

```python
class WarningSign:                   # catalog.yaml entry
    id: str
    name: str
    buckets: list[int]
    definition: str
    keywords: list[str]
    historical_example: str
    invalidator: str

class Catalog:
    version: int
    catalog_version: str             # bump → triggers replay
    warning_signs: list[WarningSign]

class MatchedWarningSign:
    id: str
    name: str
    bucket_id: int
    fit_strength: float              # 0..1

class RedTeamResult:                 # LLM/heuristic output
    bearish_thesis: str
    matched_warning_signs: list[MatchedWarningSign]
    matched_buckets: list[int]
    severity_of_concern: int         # 1..5
    invalidator: str
    confidence: float                # 0..1

class RedTeamCandidate:              # input bundle
    score_event_id: str
    article_event_id: str
    title: str
    excerpt: str
    source: str | None
    source_tier: int | None
    published_at: datetime | None
    ticker: str
    company_name: str | None
    pct_nav: float
    conviction_tier: int
    stage: str
    thesis: str
    nearest_catalyst_days: int | None
    primary_bucket_id: int
    primary_bucket_name: str
    composite: float
    threshold_band: str
    scorer_axes: tuple[float, float, float]
    scorer_rationale: str
    scorer_confidence: float
```

### Phase 5 — Outputs (`outputs/schema.py`)

```python
class AlertRecord:                   # what format.render_alert() takes
    score_event_id: str
    article_event_id: str
    red_team_event_id: str | None
    article_title: str
    article_url: str
    source: str | None
    source_tier: int | None
    ticker: str
    company_name: str | None
    pct_nav: float
    conviction_tier: int
    stage: str
    nearest_catalyst_days: int | None
    bucket_id: int
    bucket_short_name: str
    composite: float
    threshold_band: str
    axes: tuple[float, float, float]
    scorer_rationale: str
    bearish_thesis: str | None
    severity_of_concern: int | None
    matched_warning_signs: list[dict]
    invalidator: str | None

class DigestEvent:                   # one row in digest "today's events"
    ticker: str
    composite: float
    threshold_band: str
    bucket_id: int
    bucket_short_name: str
    title: str
    url: str
    source: str | None
    source_tier: int | None
    axes: tuple[float, float, float]
    scorer_rationale: str
    bearish_thesis: str | None
    severity_of_concern: int | None
    matched_warning_signs: list[dict]

class HoldingSnapshot:
    ticker: str
    pct_nav: float
    conviction_tier: int
    stage: str
    nearest_catalyst_days: int | None
    has_overdue_catalyst: bool

class DigestSummary:
    date: str
    events_by_ticker: dict[str, list[DigestEvent]]
    quiet_holdings: list[HoldingSnapshot]
    bucket_concentrations: list[tuple[int, int]]
    coverage_audit: dict[int, int]
    silent_buckets: list[int]
    narrative: str | None
    assembled_at: datetime
```

### Workstream 3 — Decision (`decision/schema.py`)

```python
Verdict = Literal["hold", "watch", "sell"]
Color = Literal["green", "yellow", "red"]
VERDICT_COLOR = {"hold": "green", "watch": "yellow", "sell": "red"}

class ScoreEvidence:                 # compact scores-row projection
    score_event_id: str
    title: str
    primary_bucket_id: int
    composite: float
    threshold_band: str
    rationale: str

class BearEvidence:                  # compact red_team_passes projection
    pass_event_id: str
    title: str
    bearish_thesis: str
    severity_of_concern: int
    matched_patterns: list[str]
    invalidator: str

class DecisionCandidate:             # per-holding evidence bundle
    ticker: str
    company_name: str | None
    stage: str
    conviction_tier: int
    thesis: str
    thesis_doc_text: str = ""        # W4: combined_text() of uploaded thesis docs
    pct_nav: float
    market_value: float
    cost_basis: float | None
    open_pnl: float | None           # market_value - cost_basis
    pnl_pct: float | None
    nearest_catalyst_days: int | None
    has_overdue_catalyst: bool
    catalysts: list[str]
    scores: list[ScoreEvidence]
    bears: list[BearEvidence]
    fmp_metrics: dict | None = None  # W2 enrichment; None until then
    max_severity: int = 1
    max_composite: float = 0.0

class PositionDecision:              # engine output → position_decisions
    ticker: str
    verdict: Verdict
    color: Color                     # derived from verdict, not from the model
    note: str                        # 4–5 line plain-English drift assessment
    drivers: list[str]
    confidence: float
    thesis_hash: str                 # changes when the thesis text changes
    inputs_hash: str                 # changes when thesis OR evidence set changes
    model_used: str
    decided_at: datetime
```

---

## 5. SQLite table schemas (on-disk persistence)

All tables live in `data/sma.db`. Every artifact also writes a row into the
universal `events` table for cross-phase idempotency lookup.

### Phase 0 — Universal events table (`db.py`)

```sql
CREATE TABLE events (
    event_id    TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    ticker      TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    payload     TEXT
);
```

### Phase 1 — Portfolio (`portfolio/store.py`)

```sql
CREATE TABLE position_pulls (
    pull_id     TEXT PRIMARY KEY,
    pulled_at   TEXT NOT NULL,
    nav         REAL NOT NULL,
    source      TEXT NOT NULL,
    raw_xml     TEXT
);

CREATE TABLE positions (
    event_id      TEXT PRIMARY KEY,
    pull_id       TEXT NOT NULL REFERENCES position_pulls(pull_id),
    ticker        TEXT NOT NULL,
    qty           REAL NOT NULL,
    market_value  REAL NOT NULL,
    pct_nav       REAL NOT NULL,
    cost_basis    REAL,
    pulled_at     TEXT NOT NULL,
    nav           REAL NOT NULL
);

-- W4: uploaded thesis documents (portfolio/uploads.py). Paths are relative to
-- DATA_ROOT; text_path is the cached extracted-text sidecar. Idempotent on
-- (ticker, content_sha) so identical re-uploads collapse to one row.
CREATE TABLE position_files (
    event_id      TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    filename      TEXT NOT NULL,
    stored_path   TEXT NOT NULL,       -- original, relative to DATA_ROOT
    text_path     TEXT,                -- cached .txt, relative to DATA_ROOT
    content_type  TEXT NOT NULL,       -- .txt | .md | .markdown | .pdf | .docx
    content_sha   TEXT NOT NULL,
    byte_size     INTEGER NOT NULL,
    n_chars       INTEGER NOT NULL DEFAULT 0,
    uploaded_at   TEXT NOT NULL,
    UNIQUE (ticker, content_sha)
);
```

### Phase 2 — News (`news/store.py`)

```sql
CREATE TABLE articles (
    event_id      TEXT PRIMARY KEY,           -- sha(url+title+lede)
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    excerpt       TEXT,
    source        TEXT,
    source_tier   INTEGER,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    raw_json      TEXT
);

CREATE TABLE article_tickers (              -- many-to-many
    event_id   TEXT NOT NULL REFERENCES articles(event_id),
    ticker     TEXT NOT NULL,
    PRIMARY KEY (event_id, ticker)
);

CREATE TABLE article_buckets (              -- many-to-many + confidence
    event_id    TEXT NOT NULL REFERENCES articles(event_id),
    bucket_id   INTEGER NOT NULL,
    confidence  REAL NOT NULL,
    PRIMARY KEY (event_id, bucket_id)
);

CREATE TABLE news_polls (
    poll_id      TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    ticker       TEXT,
    bucket_id    INTEGER,
    query_text   TEXT NOT NULL,
    n_results    INTEGER,
    n_new        INTEGER,
    status       TEXT NOT NULL              -- 'ok' | 'error'
);
```

### Phase 3 — Scores (`scorer/store.py`)

```sql
CREATE TABLE scores (
    event_id            TEXT PRIMARY KEY,
    article_event_id    TEXT NOT NULL REFERENCES articles(event_id),
    ticker              TEXT NOT NULL,
    primary_bucket_id   INTEGER NOT NULL,
    secondary_buckets   TEXT,                -- json
    financial_impact    REAL NOT NULL,
    narrative_shift     REAL NOT NULL,
    time_criticality    REAL NOT NULL,
    raw_avg             REAL NOT NULL,
    bucket_weight       REAL NOT NULL,
    position_weight     REAL NOT NULL,
    conviction_mult     REAL NOT NULL,
    catalyst_boost      REAL NOT NULL,
    stage_interaction   REAL NOT NULL,
    composite           REAL NOT NULL,
    threshold_band      TEXT NOT NULL,       -- above_t | t2_to_t | below_t2
    rationale           TEXT,
    confidence          REAL NOT NULL,
    model_used          TEXT NOT NULL,
    multipliers_version TEXT NOT NULL,
    inputs_hash         TEXT NOT NULL,
    scored_at           TEXT NOT NULL,
    UNIQUE (article_event_id, ticker, multipliers_version)
);
```

### Phase 4 — Red team (`red_team/store.py`)

```sql
CREATE TABLE red_team_passes (
    event_id              TEXT PRIMARY KEY,
    score_event_id        TEXT NOT NULL REFERENCES scores(event_id),
    article_event_id      TEXT NOT NULL REFERENCES articles(event_id),
    ticker                TEXT NOT NULL,
    bearish_thesis        TEXT NOT NULL,
    matched_warning_signs TEXT,              -- json
    matched_buckets       TEXT,              -- json
    severity_of_concern   INTEGER NOT NULL,  -- 1..5
    invalidator           TEXT,
    confidence            REAL NOT NULL,
    model_used            TEXT NOT NULL,
    catalog_version       TEXT NOT NULL,
    ran_at                TEXT NOT NULL,
    UNIQUE (score_event_id, catalog_version)
);
```

### Phase 5 — Outputs (`outputs/store.py`)

```sql
CREATE TABLE alerts (
    event_id            TEXT PRIMARY KEY,
    score_event_id      TEXT NOT NULL UNIQUE REFERENCES scores(event_id),
    article_event_id    TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    bucket_id           INTEGER,
    composite           REAL NOT NULL,
    severity_of_concern INTEGER,
    rendered_text       TEXT NOT NULL,
    channels_sent       TEXT,                -- json
    alerted_at          TEXT NOT NULL,
    suppressed          INTEGER NOT NULL DEFAULT 0,
    suppression_reason  TEXT
);

CREATE TABLE digests (
    digest_id      TEXT PRIMARY KEY,
    date           TEXT NOT NULL UNIQUE,
    rendered_md    TEXT NOT NULL,
    n_events       INTEGER NOT NULL,
    used_narrative INTEGER NOT NULL DEFAULT 0,
    file_path      TEXT,
    assembled_at   TEXT NOT NULL
);

CREATE TABLE feedback (
    event_id     TEXT PRIMARY KEY,
    target_id    TEXT NOT NULL,
    target_kind  TEXT NOT NULL,              -- alert | score | digest_event
    mark         TEXT NOT NULL,              -- useful | noise
    note         TEXT,
    marked_at    TEXT NOT NULL
);

CREATE TABLE missed_events (
    event_id          TEXT PRIMARY KEY,
    ticker            TEXT,
    bucket_id_guess   INTEGER,
    description       TEXT NOT NULL,
    article_url       TEXT,
    note              TEXT,
    recorded_at       TEXT NOT NULL
);
```

### Phase 6 — Orchestrator (`orchestrator/store.py`)

```sql
CREATE TABLE cost_ledger (
    event_id            TEXT PRIMARY KEY,
    kind                TEXT NOT NULL,       -- score | red_team | digest_narrative
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL,
    related_event_id    TEXT,
    incurred_at         TEXT NOT NULL
);

CREATE TABLE system_flags (
    flag_name      TEXT PRIMARY KEY,
    set_at         TEXT NOT NULL,
    cleared_at     TEXT,
    metadata       TEXT,                     -- json
    active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE dead_letters (
    event_id          TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,         -- score | red_team
    article_event_id  TEXT,
    ticker            TEXT,
    error             TEXT NOT NULL,
    attempt_count     INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'pending',  -- pending | abandoned
    first_failed_at   TEXT NOT NULL,
    last_attempt_at   TEXT NOT NULL
);
```

### Workstream 3 — Decision (`decision/store.py`)

```sql
CREATE TABLE position_decisions (
    event_id          TEXT PRIMARY KEY,
    ticker            TEXT NOT NULL,
    verdict           TEXT NOT NULL,        -- hold | watch | sell
    color             TEXT NOT NULL,        -- green | yellow | red
    note              TEXT NOT NULL,        -- 4–5 line drift assessment
    drivers           TEXT,                 -- json array of evidence phrases
    confidence        REAL NOT NULL,
    thesis_hash       TEXT NOT NULL,
    inputs_hash       TEXT NOT NULL,
    model_used        TEXT NOT NULL,        -- codex-cli | heuristic-v1
    decision_version  TEXT NOT NULL,
    decided_at        TEXT NOT NULL,
    UNIQUE (ticker, inputs_hash, decision_version)
);
-- latest_decisions() = most recent decided_at per ticker (dashboard + 9 AM email feed).
```

---

## 6. Versioning hinges

| Hinge | Where | What changes trigger |
|---|---|---|
| `event_id` | `identity.py` (sha256 of canonical fields) | Per-artifact uniqueness; same inputs → same id forever |
| `MULTIPLIERS_VERSION` | `scorer/multipliers.py:19` | Bump → every (article, ticker) re-scored cleanly |
| `catalog_version` | `data/warning_signs/catalog.yaml` | Bump → every above-T₂ score re-red-teamed |
| `Taxonomy.version` | `data/factor_buckets/taxonomy.yaml` | Bump on bucket-set changes; no auto-replay |
| `DECISION_VERSION` | `decision/engine.py` | Bump → every holding's thesis-drift decision re-computed |
| `inputs_hash` | `decision/engine.py` | Changes when a holding's thesis OR evidence set (score/red-team ids) changes → re-compute |

---

## 7. Operational state

| Flag | Where set | Cleared when |
|---|---|---|
| `stale_positions` | Flex pull fails or >12h old | Next successful pull |
| `exa_failure` | News poll fails | Next successful poll |
| `budget_degraded` | Spend ≥ 60% of `DAILY_BUDGET_USD` | Next day (ledger resets at UTC midnight) |
| `scorer_dead_letter` | Score attempt abandoned after retry | Manual via `retry-dead-letters` success |
| `red_team_dead_letter` | Red team attempt abandoned | Manual via `retry-dead-letters` success |

| Cascade step | Trigger | Effect |
|---|---|---|
| 1 | 60% budget | Red-team only above T (skip T₂–T band) |
| 2 | 75% budget | Skip Opus narrative on the daily digest (template fallback) |
| 3 | 85% budget | Skip buckets #10 (literature) + #11 (policy) |
| 4 | 95% budget | Skip bucket #12 (microstructure) |

## 8. Deployment (W8 — `systemd/`)

Always-on VM. `sma-api.service` runs the API + dashboard (bound to localhost).
The daily ET firings run EITHER as three timer units OR the single run-loop —
never both:

| Firing (ET) | Timer unit | Run-loop covers it | CLI |
|---|---|---|---|
| 09:00 thesis email | `sma-thesis-email.timer` | ✓ `orchestrator run` | `orchestrator thesis-email` |
| 18:00 collect+decide | `sma-collect.timer` | ✓ | `orchestrator collect` |
| 21:00 dispatch digest | `sma-dispatch.timer` | ✓ | `orchestrator dispatch` |

Timers use `OnCalendar=… America/New_York` (systemd ≥ 252, DST-aware);
`sma-monitor.service` resolves ET via Python `zoneinfo` (any version).
LLM auth = `codex login` on the host (no API key). See `systemd/README.md`.

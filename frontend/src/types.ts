// Wire types mirroring src/sma_monitor/api/schemas.py. Kept in sync by hand;
// the API is the source of truth.

export type Verdict = 'hold' | 'watch' | 'sell'
export type Color = 'green' | 'yellow' | 'red'
// Letter grade: A strong hold · B/C hold · D sell.
export type Grade = 'A' | 'B' | 'C' | 'D'
export type RatingAction = 'hold' | 'sell'
export type AttentionState = 'clean' | 'monitor' | 'watch' | 'broken'
export type TechnicalState = 'above_ema20' | 'below_ema20' | 'extended_below_ema20' | 'no_price_data'
export type ThesisSource = 'pm' | 'ai_generated' | 'system_stub'
export type ThesisStatus = 'active' | 'draft'

// Latest thesis-drift decision for a position.
export interface Decision {
  verdict: Verdict
  color: Color
  grade: Grade
  note: string
  drivers: string[]
  confidence: number
  model_used: string
  compute_source?: string
  decided_at: string
}

// Canonical synchronized rating. Prefer this over legacy Decision.
export interface Rating {
  action: RatingAction
  grade: Grade
  attention_state: AttentionState
  risk_score: number
  risk_components: Record<string, number>
  latest_close: number | null
  ema20: number | null
  price_vs_ema20_pct: number | null
  technical_state: TechnicalState
  deterministic_grade: Grade
  llm_grade: Grade | null
  final_grade: Grade
  note: string
  drivers: string[]
  confidence: number
  model_used: string
  compute_source?: string
  rating_version: string
  decided_at: string
}

// Sparkline payload with price closes and server-computed EMA20 overlay.
export interface SparklineData {
  closes: number[]
  ema20: Array<number | null>
  latest_close: number | null
  latest_ema20: number | null
  price_vs_ema20_pct: number | null
  technical_state: TechnicalState
}

// One upcoming catalyst.
export interface Catalyst {
  date: string
  type: string
  description: string
  confidence: string
  resolved: boolean
}

// One uploaded thesis document (metadata).
export interface FileMeta {
  event_id: string
  filename: string
  content_type: string
  n_chars: number
  byte_size: number
  uploaded_at: string
}

// One scored article in the detail view.
export interface ScoreItem {
  score_event_id: string
  title: string
  url: string | null
  primary_bucket_id: number
  composite: number
  threshold_band: string
  axes: [number, number, number]
  rationale: string
  confidence: number
  scored_at: string
}

// One red-team bear case in the detail view.
export interface RedTeamItem {
  pass_event_id: string
  title: string
  url: string | null
  bearish_thesis: string
  severity_of_concern: number
  matched_patterns: string[]
  invalidator: string
  ran_at: string
}

// One row in the positions grid.
export interface PositionSummary {
  ticker: string
  company_name: string | null
  stage: string
  conviction_tier: number
  qty: number
  market_value: number
  cost_basis: number | null
  pct_nav: number
  open_pnl: number | null
  pnl_pct: number | null
  nearest_catalyst_days: number | null
  has_overdue_catalyst: boolean
  thesis: string
  thesis_source: ThesisSource
  thesis_status: ThesisStatus
  is_ai_generated_thesis: boolean
  thesis_generated_by: string | null
  thesis_generated_at: string | null
  thesis_compute_source: string | null
  draft_rating_grade: Grade | null
  draft_rating_note: string | null
  draft_rating_confidence: number | null
  draft_rating_drivers: string[]
  n_files: number
  spark: SparklineData | null
  rating: Rating | null
  decision: Decision | null
}

// GET /api/positions envelope.
export interface PositionsResponse {
  pulled_at: string | null
  positions: PositionSummary[]
  missing_sidecars: string[]
}

// GET /api/positions/{ticker} detail.
export interface PositionDetail extends PositionSummary {
  catalysts: Catalyst[]
  scores: ScoreItem[]
  red_team: RedTeamItem[]
  files: FileMeta[]
  financials: Record<string, unknown> | null
}

// POST recompute response.
export interface RecomputeResponse {
  ticker: string
  scheduled: boolean
  request_id: string | null
  queue_status: string | null
  rating: Rating | null
  decision: Decision | null
  refresh?: Record<string, unknown> | null
}

export interface RecomputeStatusResponse {
  request_id: string
  command: string
  ticker: string | null
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  refresh: Record<string, unknown> | null
  error: string | null
}

// POST /api/positions/recompute (whole-portfolio) response.
export interface RecomputeAllResponse {
  scheduled: boolean
  request_id: string | null
  queue_status: string | null
  decided: number
  skipped: number
  errors: number
  holdings: number
  refresh?: Record<string, unknown> | null
}

export interface DeleteHoldingResponse {
  ticker: string
  deleted: Record<string, number>
}

export interface ManualPositionPayload {
  ticker: string
  portfolio_weight_pct: number
  thesis: string
  cost_basis_per_share?: number | null
}

export interface ManualPositionResponse {
  position: PositionSummary
  refresh?: Record<string, unknown> | null
}

export type ChatRole = 'user' | 'assistant'

export interface ChatHistoryMessage {
  role: ChatRole
  content: string
}

export interface ChatAttachmentMeta {
  filename: string
  content_type: string
  byte_size: number
  n_chars: number
  parser: string
}

export interface ChatResponse {
  answer: string
  model_used: string
  used_tickers: string[]
  cited_context: Array<Record<string, unknown>>
  data_freshness: Record<string, unknown>
  attachments: ChatAttachmentMeta[]
}

export interface ChatQueuedResponse {
  scheduled: true
  request_id: string
  command: string
  ticker: string | null
  status: 'queued'
  requested_at: string
  message: string
}

export interface ChatStatusResponse {
  request_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  result: ChatResponse | null
  error: string | null
}

export type ChatSubmitResponse = ChatResponse | ChatQueuedResponse

// GET /api/quotes — intraday price quotes during trading hours.
export interface QuotesResponse {
  quotes: Record<string, number>
  is_market_open: boolean
  source: string
}

// POST /api/portfolio/pull response.
export interface IBKRPullResponse {
  pull_id: string
  nav: number
  ticker_count: number
  pulled_at: string
  new_tickers: string[]
  removed_tickers: string[]
}

// GET /api/status snapshot.
export interface Status {
  spend: { spent_usd: number; budget_usd: number; fraction_spent: number }
  degrade: Record<string, boolean>
  spend_by_kind: Array<Record<string, unknown>>
  flags: Array<Record<string, unknown>>
  dead_letters: { pending: number; recent: Array<Record<string, unknown>> }
  positions: { count: number; pulled_at: string | null; missing_sidecars: string[] }
}

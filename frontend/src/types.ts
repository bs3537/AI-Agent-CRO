// Wire types mirroring src/sma_monitor/api/schemas.py. Kept in sync by hand;
// the API is the source of truth.

export type Verdict = 'hold' | 'watch' | 'sell'
export type Color = 'green' | 'yellow' | 'red'

// Latest thesis-drift decision for a position.
export interface Decision {
  verdict: Verdict
  color: Color
  note: string
  drivers: string[]
  confidence: number
  model_used: string
  decided_at: string
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
  n_files: number
  spark: number[] | null
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
  decision: Decision | null
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

// Typed fetch client for the SMA Monitor API. Paths are relative so the Vite
// dev proxy (/api → :8000) and the production same-origin static mount both
// work without a configured base URL.

import type {
  PositionDetail,
  PositionsResponse,
  PositionSummary,
  DeleteHoldingResponse,
  ChatHistoryMessage,
  ChatStatusResponse,
  ChatSubmitResponse,
  IBKRPullResponse,
  ManualPositionPayload,
  ManualPositionResponse,
  QuotesResponse,
  RecomputeAllResponse,
  RecomputeResponse,
  RecomputeStatusResponse,
  Status,
} from './types'

// Parse a JSON response, throwing a useful Error on non-2xx so callers can
// surface the message.
async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.text().catch(() => '')
    throw new Error(`${r.status} ${r.statusText}${body ? `: ${body}` : ''}`)
  }
  return r.json() as Promise<T>
}

function withTimeout(ms: number): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController()
  const id = window.setTimeout(() => controller.abort(), ms)
  return { signal: controller.signal, cleanup: () => window.clearTimeout(id) }
}

async function fetchJsonWithTimeout<T>(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<T> {
  const timeout = withTimeout(timeoutMs)
  try {
    return await fetch(url, { ...init, signal: timeout.signal }).then(json<T>)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 60000)} minutes`)
    }
    throw e
  } finally {
    timeout.cleanup()
  }
}

// The API surface used by the dashboard.
export const api = {
  // Grid of positions with P&L + latest decision.
  positions: () => fetch('/api/positions').then(json<PositionsResponse>),

  // Full detail (scores, red-team, files, catalysts) for one ticker.
  detail: (ticker: string) =>
    fetch(`/api/positions/${ticker}`).then(json<PositionDetail>),

  // Manually add a dashboard position and run a fresh analysis.
  addManualPosition: (payload: ManualPositionPayload) =>
    fetch('/api/positions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(json<ManualPositionResponse>),

  // Replace a ticker's thesis; returns the refreshed summary.
  setThesis: (ticker: string, thesis: string) =>
    fetch(`/api/positions/${ticker}/thesis`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thesis }),
    }).then(json<PositionSummary>),

  // Upload a thesis document (multipart).
  uploadFile: (ticker: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`/api/positions/${ticker}/files`, { method: 'POST', body: fd }).then(
      json<PositionDetail['files'][number]>,
    )
  },

  // Remove an uploaded document.
  deleteFile: async (ticker: string, eventId: string) => {
    const r = await fetch(`/api/positions/${ticker}/files/${eventId}`, { method: 'DELETE' })
    if (!r.ok && r.status !== 204) throw new Error(`${r.status} ${r.statusText}`)
  },

  // Delete a holding and its ticker-owned monitor data.
  deleteHolding: (ticker: string) =>
    fetch(`/api/positions/${ticker}`, { method: 'DELETE' }).then(json<DeleteHoldingResponse>),

  // Recompute one ticker's decision. wait=true runs inline and returns it.
  recompute: (ticker: string, wait = true, computeSource = 'manual_single') => {
    const params = new URLSearchParams({
      wait: String(wait),
      compute_source: computeSource,
    })
    return fetchJsonWithTimeout<RecomputeResponse>(
      `/api/positions/${ticker}/recompute?${params}`,
      { method: 'POST' },
      10 * 60 * 1000,
    )
  },

  // Poll a dashboard-queued recompute request completed by the VPS runner.
  recomputeStatus: (requestId: string) =>
    fetch(`/api/positions/recompute/${requestId}`).then(json<RecomputeStatusResponse>),

  // Recompute every holding's decision in one call. Defaults to background
  // (wait=false) so the request returns immediately; the grid poll surfaces
  // each decision as it lands.
  recomputeAll: (wait = false) =>
    fetch(`/api/positions/recompute?wait=${wait}`, { method: 'POST' }).then(
      json<RecomputeAllResponse>,
    ),

  // Trigger a live IBKR Flex pull and persist results. Returns a diff of
  // new vs removed tickers relative to the previous pull.
  pullFromIBKR: () =>
    fetchJsonWithTimeout<IBKRPullResponse>(
      '/api/portfolio/pull',
      { method: 'POST' },
      3 * 60 * 1000,
    ),

  // Operational snapshot for the status bar.
  status: () => fetch('/api/status').then(json<Status>),

  // Intraday quotes for all held tickers (+ is_market_open flag).
  quotes: () => fetch('/api/quotes').then(json<QuotesResponse>),

  // Portfolio chatbot. Uses multipart even with no files so uploads share the
  // same endpoint.
  chat: ({
    message,
    history,
    files = [],
    ticker,
    includePortfolio = true,
  }: {
    message: string
    history: ChatHistoryMessage[]
    files?: File[]
    ticker?: string | null
    includePortfolio?: boolean
  }) => {
    const fd = new FormData()
    fd.append('message', message)
    fd.append('history', JSON.stringify(history))
    fd.append('include_portfolio', String(includePortfolio))
    if (ticker) fd.append('ticker', ticker)
    for (const file of files) fd.append('files', file)
    return fetch('/api/chat', { method: 'POST', body: fd }).then(json<ChatSubmitResponse>)
  },

  // Poll a dashboard-queued chat request completed by the VPS Codex runner.
  chatStatus: (requestId: string) =>
    fetch(`/api/chat/${requestId}`).then(json<ChatStatusResponse>),
}

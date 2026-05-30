// Typed fetch client for the SMA Monitor API. Paths are relative so the Vite
// dev proxy (/api → :8000) and the production same-origin static mount both
// work without a configured base URL.

import type {
  PositionDetail,
  PositionsResponse,
  PositionSummary,
  DeleteHoldingResponse,
  RecomputeAllResponse,
  RecomputeResponse,
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

// The API surface used by the dashboard.
export const api = {
  // Grid of positions with P&L + latest decision.
  positions: () => fetch('/api/positions').then(json<PositionsResponse>),

  // Full detail (scores, red-team, files, catalysts) for one ticker.
  detail: (ticker: string) =>
    fetch(`/api/positions/${ticker}`).then(json<PositionDetail>),

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
    return fetch(`/api/positions/${ticker}/recompute?${params}`, { method: 'POST' }).then(
      json<RecomputeResponse>,
    )
  },

  // Recompute every holding's decision in one call. Defaults to background
  // (wait=false) so the request returns immediately; the grid poll surfaces
  // each decision as it lands.
  recomputeAll: (wait = false) =>
    fetch(`/api/positions/recompute?wait=${wait}`, { method: 'POST' }).then(
      json<RecomputeAllResponse>,
    ),

  // Operational snapshot for the status bar.
  status: () => fetch('/api/status').then(json<Status>),
}

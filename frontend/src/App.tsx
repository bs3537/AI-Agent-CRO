import { useCallback, useEffect, useState } from 'react'
import AppBar from '@mui/material/AppBar'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import Container from '@mui/material/Container'
import IconButton from '@mui/material/IconButton'
import LinearProgress from '@mui/material/LinearProgress'
import Toolbar from '@mui/material/Toolbar'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import AddIcon from '@mui/icons-material/Add'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import RefreshIcon from '@mui/icons-material/Refresh'
import { api } from './api'
import type { ManualPositionPayload, PositionsResponse, QuotesResponse, Status } from './types'
import AddPositionDrawer from './components/AddPositionDrawer'
import BrandLogo from './components/BrandLogo'
import ChatPanel from './components/ChatPanel'
import PositionCard from './components/PositionCard'
import DetailDrawer from './components/DetailDrawer'
import ThesisDrawer, { type ThesisPackage } from './components/ThesisDrawer'
import StatusBar from './components/StatusBar'

// Poll cadence for the grid + status (decisions refresh after batch runs).
const POLL_MS = 30_000
// Intraday quotes refresh every 30 min during trading hours.
const QUOTES_POLL_MS = 30 * 60 * 1_000
const RECOMPUTE_STATUS_POLL_MS = 1_500
const RECOMPUTE_STATUS_TIMEOUT_MS = 10 * 60 * 1000

async function waitForQueuedRecompute(requestId: string): Promise<void> {
  const deadline = Date.now() + RECOMPUTE_STATUS_TIMEOUT_MS
  let lastStatus: string = 'queued'
  while (Date.now() < deadline) {
    await sleep(RECOMPUTE_STATUS_POLL_MS)
    const status = await api.recomputeStatus(requestId)
    lastStatus = status.status
    if (status.status === 'succeeded') return
    if (status.status === 'failed') {
      throw new Error(status.error || 'VPS Codex recompute request failed')
    }
  }
  throw new Error(`VPS Codex recompute request is still ${lastStatus}; try again in a minute.`)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

// Dashboard root: loads positions + status, polls on an interval, and wires
// the per-card mutations (thesis edit, upload, recompute) back to the API,
// refreshing the grid after each.
export default function App() {
  const [data, setData] = useState<PositionsResponse | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [detailTicker, setDetailTicker] = useState<string | null>(null)
  const [thesisTicker, setThesisTicker] = useState<string | null>(null)
  const [addPositionOpen, setAddPositionOpen] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [chatWidth, setChatWidth] = useState(480)
  const [recomputingAll, setRecomputingAll] = useState(false)
  const [recomputeAllSecs, setRecomputeAllSecs] = useState(0)
  const [recomputeQueue, setRecomputeQueue] = useState<{
    ticker: string
    index: number
    total: number
  } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [quotes, setQuotes] = useState<QuotesResponse | null>(null)
  const [quotesAt, setQuotesAt] = useState<Date | null>(null)

  useEffect(() => {
    if (!recomputingAll) { setRecomputeAllSecs(0); return }
    setRecomputeAllSecs(0)
    const id = setInterval(() => setRecomputeAllSecs((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [recomputingAll])

  // Resets intraday state so the UI reverts to EOD data immediately.
  // Called on market close, fetch error, or unmount.
  const clearLiveMode = useCallback(() => {
    setQuotes(prev => prev ? { ...prev, is_market_open: false, quotes: {} } : null)
  }, [])

  // Fetch intraday quotes from FMP. Returns true when the market is open so
  // the polling effect knows whether to continue. On any error the live state
  // is cleared so the UI falls back to EOD data instead of showing stale prices.
  const fetchQuotes = useCallback(async (): Promise<boolean> => {
    try {
      const q = await api.quotes()
      setQuotes(q)
      if (Object.keys(q.quotes).length > 0) setQuotesAt(new Date())
      return q.is_market_open
    } catch {
      clearLiveMode()
      return false
    }
  }, [clearLiveMode])

  // Returns milliseconds from now until 16:00 ET (market close).
  // Uses Intl.DateTimeFormat to read the current ET clock, then computes the
  // remaining ms. Returns 0 if close is already past.
  const msUntilClose = (): number => {
    const now = new Date()
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: 'numeric',
      second: 'numeric',
      hour12: false,
    }).formatToParts(now)
    const get = (t: string) => parseInt(parts.find(p => p.type === t)?.value ?? '0')
    const etSecondsNow = get('hour') * 3600 + get('minute') * 60 + get('second')
    const etSecondsClose = 16 * 3600
    return Math.max(0, (etSecondsClose - etSecondsNow) * 1000)
  }

  // Market-state driven polling: fetch once on mount to check status, then
  // start a 30-min interval only when the market is open. A hard-stop timeout
  // fires at exactly 16:00 ET to clear the interval without waiting for the
  // next 30-min tick. Whenever polling stops (close timeout, tick returning
  // closed, error, or unmount) clearLiveMode() is called so the UI reverts to
  // EOD data immediately without requiring a page reload.
  useEffect(() => {
    let intervalId: ReturnType<typeof setInterval> | null = null
    let closeId: ReturnType<typeof setTimeout> | null = null

    const stopPolling = () => {
      if (intervalId !== null) { clearInterval(intervalId); intervalId = null }
      if (closeId !== null) { clearTimeout(closeId); closeId = null }
      clearLiveMode()
    }

    const start = async () => {
      const isOpen = await fetchQuotes()
      if (!isOpen) { clearLiveMode(); return }

      // Schedule hard-stop at 16:00 ET.
      const msToClose = msUntilClose()
      closeId = setTimeout(stopPolling, msToClose)

      intervalId = setInterval(async () => {
        const stillOpen = await fetchQuotes()
        if (!stillOpen) stopPolling()
      }, QUOTES_POLL_MS)
    }

    void start()
    return stopPolling
  }, [fetchQuotes, clearLiveMode])

  // Refetch the grid + status snapshot.
  const refresh = useCallback(async () => {
    try {
      const [positions, st] = await Promise.all([api.positions(), api.status()])
      setData(positions)
      setStatus(st)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }, [])

  // Initial load + polling loop.
  useEffect(() => {
    void refresh()
    const id = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(id)
  }, [refresh])

  const onSaveThesisPackage = useCallback(
    async ({ ticker, thesis, files, replaceFiles }: ThesisPackage) => {
      await api.setThesis(ticker, thesis)
      if (replaceFiles) {
        const detail = await api.detail(ticker)
        for (const file of detail.files) {
          await api.deleteFile(ticker, file.event_id)
        }
      }
      for (const file of files) {
        await api.uploadFile(ticker, file)
      }
      const recompute = await api.recompute(ticker, true)
      await refresh()
      setThesisTicker(null)
      setNotice(
        recompute.scheduled
          ? `${ticker} thesis package saved; VPS Codex recompute queued.`
          : `${ticker} thesis package saved and analysis recomputed.`,
      )
    },
    [refresh],
  )

  // Upload a doc, then rerun the holding analysis because uploaded text feeds
  // the next LLM decision candidate.
  const onUpload = useCallback(
    async (ticker: string, file: File) => {
      await api.uploadFile(ticker, file)
      const recompute = await api.recompute(ticker, true)
      if (recompute.scheduled && recompute.request_id) {
        setNotice(`${ticker} document uploaded and recompute queued for VPS runner; waiting for completion.`)
        await waitForQueuedRecompute(recompute.request_id)
      }
      await refresh()
      setNotice(
        recompute.scheduled
          ? `${ticker} document uploaded and analysis recomputed by VPS runner.`
          : `${ticker} document uploaded and analysis recomputed.`,
      )
    },
    [refresh],
  )

  // Refresh latest ticker evidence, then recompute one position synchronously.
  const onRecompute = useCallback(
    async (ticker: string) => {
      try {
        const recompute = await api.recompute(ticker, true)
        if (recompute.scheduled && recompute.request_id) {
          setNotice(`${ticker} recompute queued for VPS runner; waiting for completion.`)
          await waitForQueuedRecompute(recompute.request_id)
        }
        await refresh()
        setNotice(
          recompute.scheduled
            ? `${ticker} analysis recomputed by VPS runner.`
            : `${ticker} analysis recomputed.`,
        )
      } catch (e) {
        setError(`${ticker} recompute failed: ${String(e)}`)
        await refresh().catch(() => undefined)
      }
    },
    [refresh],
  )

  const onDeleteHolding = useCallback(
    async (ticker: string) => {
      const res = await api.deleteHolding(ticker)
      if (detailTicker === ticker) setDetailTicker(null)
      if (thesisTicker === ticker) setThesisTicker(null)
      await refresh()
      const deletedRows = Object.values(res.deleted).reduce((a, b) => a + b, 0)
      setNotice(`${ticker} deleted from the dashboard (${deletedRows} stored row${deletedRows === 1 ? '' : 's'} removed).`)
    },
    [refresh, detailTicker, thesisTicker],
  )

  const onAddManualPosition = useCallback(
    async (payload: ManualPositionPayload) => {
      const res = await api.addManualPosition(payload)
      await refresh()
      setNotice(`${res.position.ticker} added and analyzed.`)
    },
    [refresh],
  )

  // Recompute the portfolio one ticker at a time, largest %NAV first. This
  // avoids stacking many Codex/LLM calls at once and refreshes the grid after
  // each finished holding so the tile updates immediately.
  const onRecomputeAll = useCallback(async () => {
    const queue = [...(data?.positions ?? [])].sort((a, b) => b.pct_nav - a.pct_nav)
    if (queue.length === 0) return
    setRecomputingAll(true)
    setNotice(null)
    try {
      for (let i = 0; i < queue.length; i += 1) {
        const pos = queue[i]
        setRecomputeQueue({ ticker: pos.ticker, index: i + 1, total: queue.length })
        setNotice(`Recomputing ${i + 1}/${queue.length}: ${pos.ticker}`)
        const recompute = await api.recompute(pos.ticker, true, 'manual_all')
        if (recompute.scheduled && recompute.request_id) {
          setNotice(`Recompute queued for ${pos.ticker}; waiting for VPS runner (${i + 1}/${queue.length}).`)
          await waitForQueuedRecompute(recompute.request_id)
        }
        await refresh()
      }
      setNotice(`Evidence refresh + recompute finished for ${queue.length} position${queue.length === 1 ? '' : 's'}.`)
    } catch (e) {
      setError(String(e))
    } finally {
      setRecomputingAll(false)
      setRecomputeQueue(null)
    }
  }, [refresh, data])

  // Book-wide staleness: true while the orchestrator's stale_positions flag is
  // active (failed/old Flex pull). Passed to every tile as a STALE DATA badge.
  const stale = (status?.flags ?? []).some(
    (f) => f.flag_name === 'stale_positions' && Boolean(f.active),
  )
  const isMarketOpen = quotes?.is_market_open ?? false
  const liveQuotes = quotes?.quotes ?? {}
  const thesisPosition =
    data?.positions.find((p) => p.ticker === thesisTicker) ?? null

  return (
    <Box sx={{ pb: 6 }}>
      <AppBar position="sticky" color="default" enableColorOnDark elevation={0}>
        <Toolbar sx={{ gap: 2 }}>
          <BrandLogo />
          <Box sx={{ flexGrow: 1 }} />
          <StatusBar status={status} />
          <Button
            variant="outlined"
            color="primary"
            size="small"
            startIcon={<AddIcon />}
            disabled={recomputingAll}
            onClick={() => setAddPositionOpen(true)}
            sx={{ ml: 1.5, whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            Add position
          </Button>
          <Tooltip title="Open AI CRO chat">
            <IconButton
              color={chatOpen ? 'primary' : 'default'}
              size="small"
              onClick={() => setChatOpen((v) => !v)}
              sx={{ flexShrink: 0 }}
            >
              <AutoAwesomeIcon />
            </IconButton>
          </Tooltip>
          <Button
            variant="contained"
            color="primary"
            size="small"
            startIcon={<RefreshIcon />}
            disabled={recomputingAll || !data || data.positions.length === 0}
            onClick={() => void onRecomputeAll()}
            sx={{ ml: 1.5, whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            {recomputingAll && recomputeQueue
              ? `${recomputeQueue.index}/${recomputeQueue.total} ${recomputeQueue.ticker} · ${recomputeAllSecs}s`
              : 'Recompute all'}
          </Button>
        </Toolbar>
        {!data && !error && <LinearProgress color="primary" />}
      </AppBar>

      <Box sx={{ display: 'flex', alignItems: 'flex-start' }}>
        <Box sx={{ flexGrow: 1, minWidth: 0, pb: 6 }}>
          <Container maxWidth={chatOpen ? false : 'lg'} sx={{ mt: 3 }}>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {notice && (
          <Alert severity="info" sx={{ mb: 2 }} onClose={() => setNotice(null)}>
            {notice}
          </Alert>
        )}

        {data && data.missing_sidecars.length > 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Positions without a thesis sidecar (add a thesis to start monitoring):{' '}
            {data.missing_sidecars.join(', ')}
          </Alert>
        )}

        {isMarketOpen && Object.keys(liveQuotes).length > 0 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                bgcolor: 'success.main',
                flexShrink: 0,
                '@keyframes pulse': {
                  '0%': { opacity: 1 },
                  '50%': { opacity: 0.3 },
                  '100%': { opacity: 1 },
                },
                animation: 'pulse 2s ease-in-out infinite',
              }}
            />
            <Typography variant="caption" sx={{ opacity: 0.7 }}>
              Live intraday prices · {Object.keys(liveQuotes).length} tickers · refreshes every 30 min
              {quotesAt ? ` · last updated ${quotesAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
            </Typography>
            <Tooltip title="Prices from FMP, cross-checked against Yahoo Finance. Refreshed every 30 min during 9:30am–4pm ET. Open P/L, daily change %, and market value on each card reflect the current price.">
              <Chip size="small" label="LIVE" color="success" variant="outlined" sx={{ fontWeight: 700 }} />
            </Tooltip>
          </Box>
        )}

        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 460px), 1fr))',
          }}
        >
          {data?.positions.map((pos) => (
            <PositionCard
              key={pos.ticker}
              pos={pos}
              liveQuote={isMarketOpen ? (liveQuotes[pos.ticker] ?? null) : null}
              stale={stale}
              onUpload={onUpload}
              onRecompute={onRecompute}
              onDelete={onDeleteHolding}
              onOpenThesis={setThesisTicker}
              onOpenDetail={setDetailTicker}
            />
          ))}
        </Box>

        {data && data.positions.length === 0 && (
          <Typography sx={{ opacity: 0.6, mt: 4, textAlign: 'center' }}>
            No held positions with a sidecar yet.
          </Typography>
        )}
          </Container>
        </Box>
        {chatOpen && (
          <ChatPanel
            width={chatWidth}
            onWidthChange={setChatWidth}
            onClose={() => setChatOpen(false)}
          />
        )}
      </Box>

      <DetailDrawer
        ticker={detailTicker}
        onClose={() => setDetailTicker(null)}
        onChanged={() => void refresh()}
      />

      <ThesisDrawer
        ticker={thesisTicker}
        position={thesisPosition}
        onClose={() => setThesisTicker(null)}
        onSavePackage={onSaveThesisPackage}
        onChanged={() => void refresh()}
      />

      <AddPositionDrawer
        open={addPositionOpen}
        onClose={() => setAddPositionOpen(false)}
        onAdd={onAddManualPosition}
      />
    </Box>
  )
}

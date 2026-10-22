import { useCallback, useEffect, useState } from 'react'
import AppBar from '@mui/material/AppBar'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import Container from '@mui/material/Container'
import IconButton from '@mui/material/IconButton'
import LinearProgress from '@mui/material/LinearProgress'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import Toolbar from '@mui/material/Toolbar'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import AddIcon from '@mui/icons-material/Add'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import RefreshIcon from '@mui/icons-material/Refresh'
import TableRowsIcon from '@mui/icons-material/TableRows'
import ViewModuleIcon from '@mui/icons-material/ViewModule'
import { api } from './api'
import type { ManualPositionPayload, PositionsResponse, QuotesResponse, Status } from './types'
import AddPositionDrawer from './components/AddPositionDrawer'
import BrandLogo from './components/BrandLogo'
import CatalystCalendar from './components/CatalystCalendar'
import JobHealth from './components/JobHealth'
import WeightCheck from './components/WeightCheck'
import ChatPanel from './components/ChatPanel'
import PositionCard from './components/PositionCard'
import HoldingDrawer from './components/HoldingDrawer'
import KpiStrip from './components/KpiStrip'
import PositionsTable from './components/PositionsTable'
import TriageBands from './components/TriageBands'
import ThesisDrawer, { type ThesisPackage } from './components/ThesisDrawer'
import StatusBar from './components/StatusBar'

// Poll cadence for the grid + status (decisions refresh after batch runs).
const POLL_MS = 30_000
// Intraday quotes refresh every 30 min during trading hours.
const QUOTES_POLL_MS = 30 * 60 * 1_000
const VIEW_STORAGE_KEY = 'ai-cro-dashboard-view-v1'

function initialViewMode(): 'grid' | 'table' {
  try {
    return localStorage.getItem(VIEW_STORAGE_KEY) === 'grid' ? 'grid' : 'table'
  } catch {
    return 'table'
  }
}

// Dashboard root: loads positions + status, polls on an interval, and wires
// the per-card mutations (thesis edit, upload) back to the API,
// refreshing the grid after each.
function PortfolioDashboard() {
  const [data, setData] = useState<PositionsResponse | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [detailTicker, setDetailTicker] = useState<string | null>(null)
  const [thesisTicker, setThesisTicker] = useState<string | null>(null)
  const [addPositionOpen, setAddPositionOpen] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [chatWidth, setChatWidth] = useState(480)
  const [notice, setNotice] = useState<string | null>(null)
  const [quotes, setQuotes] = useState<QuotesResponse | null>(null)
  const [quotesAt, setQuotesAt] = useState<Date | null>(null)
  const [viewMode, setViewMode] = useState<'grid' | 'table'>(initialViewMode)
  const [reviewOnly, setReviewOnly] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, viewMode)
    } catch {
      // Browser privacy settings may disable storage; the in-memory choice still works.
    }
  }, [viewMode])

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
      await refresh()
      setThesisTicker(null)
      setNotice(`${ticker} thesis package saved.`)
    },
    [refresh],
  )

  // Upload a supporting document for a holding.
  const onUpload = useCallback(
    async (ticker: string, file: File) => {
      await api.uploadFile(ticker, file)
      await refresh()
      setNotice(`${ticker} document uploaded.`)
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
      setNotice(
        payload.thesis.trim()
          ? `${res.position.ticker} added and analyzed.`
          : `${res.position.ticker} added; preliminary thesis research queued.`,
      )
    },
    [refresh],
  )

  // Book-wide staleness: true while the orchestrator's stale_positions flag is
  // active (failed/old Flex pull). Passed to every tile as a STALE DATA badge.
  const stale = (status?.flags ?? []).some(
    (f) => f.flag_name === 'stale_positions' && Boolean(f.active),
  )
  const isMarketOpen = quotes?.is_market_open ?? false
  const liveQuotes = quotes?.quotes ?? {}
  const aiDraftCount = (data?.positions ?? []).filter((p) => p.is_ai_generated_thesis).length
  const tablePositions = (data?.positions ?? []).filter(
    (p) => !reviewOnly || p.is_ai_generated_thesis,
  )
  const thesisPosition =
    data?.positions.find((p) => p.ticker === thesisTicker) ?? null
  const detailPosition =
    data?.positions.find((p) => p.ticker === detailTicker) ?? null

  return (
    <Box sx={{ pb: 6 }}>
      <AppBar position="sticky" color="default" enableColorOnDark elevation={0}>
        <Toolbar
          sx={{
            gap: { xs: 1, md: 2 },
            flexWrap: { xs: 'wrap', md: 'nowrap' },
            py: { xs: 1, md: 0 },
          }}
        >
          <BrandLogo />
          <Box sx={{ flexGrow: 1 }} />
          <StatusBar status={status} />
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              flexBasis: { xs: '100%', md: 'auto' },
              justifyContent: { xs: 'flex-end', md: 'initial' },
              ml: { xs: 0, md: 1.5 },
            }}
          >
            <CatalystCalendar
              positions={data?.positions ?? []}
              onOpenDetail={setDetailTicker}
            />
            <ToggleButtonGroup
              size="small"
              exclusive
              value={viewMode}
              onChange={(_event, next: 'grid' | 'table' | null) => {
                if (next) setViewMode(next)
              }}
              sx={{ flexShrink: 0 }}
            >
              <Tooltip title="Table view">
                <ToggleButton value="table" aria-label="Table view">
                  <TableRowsIcon fontSize="small" />
                </ToggleButton>
              </Tooltip>
              <Tooltip title="Tile view">
                <ToggleButton value="grid" aria-label="Tile view">
                  <ViewModuleIcon fontSize="small" />
                </ToggleButton>
              </Tooltip>
            </ToggleButtonGroup>
            <Tooltip title="Add position">
              <Button
                variant="outlined"
                color="primary"
                size="small"
                startIcon={<AddIcon />}
                onClick={() => setAddPositionOpen(true)}
                aria-label="Add position"
                sx={{
                  minWidth: { xs: 34, sm: 'auto' },
                  px: { xs: 1, sm: 1.5 },
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                  '& .MuiButton-startIcon': { mr: { xs: 0, sm: 1 } },
                }}
              >
                <Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>
                  Add position
                </Box>
              </Button>
            </Tooltip>
            <Tooltip title="Open AI CRO chat">
              <IconButton
                aria-label="Open AI CRO chat"
                color={chatOpen ? 'primary' : 'default'}
                size="small"
                onClick={() => setChatOpen((value) => !value)}
                sx={{ flexShrink: 0 }}
              >
                <AutoAwesomeIcon />
              </IconButton>
            </Tooltip>
          </Box>
        </Toolbar>
        {!data && !error && <LinearProgress color="primary" />}
      </AppBar>

      <Box sx={{ display: 'flex', alignItems: 'flex-start' }}>
        <Box sx={{ flexGrow: 1, minWidth: 0, pb: 6 }}>
          <Container
            maxWidth={chatOpen || viewMode === 'table' ? false : 'lg'}
            sx={{ mt: 3, px: { xs: 1.5, sm: 3 } }}
          >
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

        <JobHealth />
        <WeightCheck positions={data?.positions ?? []} />

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

        {viewMode === 'grid' ? (
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
                onDelete={onDeleteHolding}
                onOpenThesis={setThesisTicker}
                onOpenDetail={setDetailTicker}
              />
            ))}
          </Box>
        ) : (
          <>
            <KpiStrip
              positions={data?.positions ?? []}
              stale={stale}
              liveQuotes={liveQuotes}
              marketOpen={isMarketOpen}
            />
            <TriageBands
              positions={data?.positions ?? []}
              liveQuotes={liveQuotes}
              marketOpen={isMarketOpen}
              onOpenDetail={setDetailTicker}
            />
            {aiDraftCount > 0 && (
              <Box sx={{ mb: 1 }}>
                <Chip
                  size="small"
                  color="info"
                  variant={reviewOnly ? 'filled' : 'outlined'}
                  label={
                    reviewOnly
                      ? `Showing ${aiDraftCount} AI drafts needing review (click to show all)`
                      : `${aiDraftCount} AI drafts need review`
                  }
                  onClick={() => setReviewOnly((v) => !v)}
                />
              </Box>
            )}
            <PositionsTable
              positions={tablePositions}
              stale={stale}
              liveQuotes={liveQuotes}
              marketOpen={isMarketOpen}
              onOpenDetail={setDetailTicker}
            />
          </>
        )}

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

      <HoldingDrawer
        ticker={detailTicker}
        position={detailPosition}
        liveQuote={
          isMarketOpen && detailTicker
            ? liveQuotes[detailTicker] ?? null
            : null
        }
        stale={stale}
        thesisOpen={Boolean(thesisTicker)}
        onClose={() => setDetailTicker(null)}
        onOpenThesis={setThesisTicker}
        onUpload={onUpload}
        onDelete={onDeleteHolding}
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

export default function App() {
  return <PortfolioDashboard />
}

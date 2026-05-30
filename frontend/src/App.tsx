import { useCallback, useEffect, useState } from 'react'
import AppBar from '@mui/material/AppBar'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Container from '@mui/material/Container'
import LinearProgress from '@mui/material/LinearProgress'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import RefreshIcon from '@mui/icons-material/Refresh'
import { api } from './api'
import type { PositionsResponse, Status } from './types'
import BrandLogo from './components/BrandLogo'
import PositionCard from './components/PositionCard'
import DetailDrawer from './components/DetailDrawer'
import ThesisDrawer, { type ThesisPackage } from './components/ThesisDrawer'
import StatusBar from './components/StatusBar'

// Poll cadence for the grid + status (decisions refresh after batch runs).
const POLL_MS = 30_000

// Dashboard root: loads positions + status, polls on an interval, and wires
// the per-card mutations (thesis edit, upload, recompute) back to the API,
// refreshing the grid after each.
export default function App() {
  const [data, setData] = useState<PositionsResponse | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [detailTicker, setDetailTicker] = useState<string | null>(null)
  const [thesisTicker, setThesisTicker] = useState<string | null>(null)
  const [recomputingAll, setRecomputingAll] = useState(false)
  const [recomputeQueue, setRecomputeQueue] = useState<{
    ticker: string
    index: number
    total: number
  } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

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
      await api.recompute(ticker, true)
      await refresh()
      setNotice(`${ticker} thesis package saved and analysis recomputed.`)
    },
    [refresh],
  )

  // Upload a doc, then rerun the holding analysis because uploaded text feeds
  // the next LLM decision candidate.
  const onUpload = useCallback(
    async (ticker: string, file: File) => {
      await api.uploadFile(ticker, file)
      await api.recompute(ticker, true)
      await refresh()
      setNotice(`${ticker} document uploaded and analysis recomputed.`)
    },
    [refresh],
  )

  // Refresh latest ticker evidence, then recompute one position synchronously.
  const onRecompute = useCallback(
    async (ticker: string) => {
      await api.recompute(ticker, true)
      await refresh()
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
        await api.recompute(pos.ticker, true, 'manual_all')
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
            variant="contained"
            color="primary"
            size="small"
            startIcon={<RefreshIcon />}
            disabled={recomputingAll || !data || data.positions.length === 0}
            onClick={() => void onRecomputeAll()}
            sx={{ ml: 1.5, whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            {recomputingAll && recomputeQueue
              ? `${recomputeQueue.index}/${recomputeQueue.total} ${recomputeQueue.ticker}`
              : 'Recompute all'}
          </Button>
        </Toolbar>
        {!data && !error && <LinearProgress color="primary" />}
      </AppBar>

      <Container maxWidth="lg" sx={{ mt: 3 }}>
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

        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
          }}
        >
          {data?.positions.map((pos) => (
            <PositionCard
              key={pos.ticker}
              pos={pos}
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
    </Box>
  )
}

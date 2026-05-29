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
import PositionCard from './components/PositionCard'
import DetailDrawer from './components/DetailDrawer'
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
  const [recomputingAll, setRecomputingAll] = useState(false)
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

  // Save a thesis, then refresh so the editor + decision reflect it.
  const onSaveThesis = useCallback(
    async (ticker: string, thesis: string) => {
      await api.setThesis(ticker, thesis)
      await refresh()
    },
    [refresh],
  )

  // Upload a doc, then refresh (file count updates on the card).
  const onUpload = useCallback(
    async (ticker: string, file: File) => {
      await api.uploadFile(ticker, file)
      await refresh()
    },
    [refresh],
  )

  // Recompute one position synchronously, then refresh to show the verdict.
  const onRecompute = useCallback(
    async (ticker: string) => {
      await api.recompute(ticker, true)
      await refresh()
    },
    [refresh],
  )

  // Kick off a whole-portfolio recompute in the background, then refresh. The
  // engine runs server-side across all holdings; decisions land over the next
  // poll cycles, so we just confirm it started rather than blocking on it.
  const onRecomputeAll = useCallback(async () => {
    setRecomputingAll(true)
    setNotice(null)
    try {
      await api.recomputeAll()
      const n = data?.positions.length ?? 0
      setNotice(`Recompute started for ${n} position${n === 1 ? '' : 's'} — the grid updates as decisions complete.`)
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setRecomputingAll(false)
    }
  }, [refresh, data])

  // Book-wide staleness: true while the orchestrator's stale_positions flag is
  // active (failed/old Flex pull). Passed to every tile as a STALE DATA badge.
  const stale = (status?.flags ?? []).some(
    (f) => f.flag_name === 'stale_positions' && Boolean(f.active),
  )

  return (
    <Box sx={{ pb: 6 }}>
      <AppBar position="sticky" color="default" enableColorOnDark elevation={0}>
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ color: 'primary.main', fontWeight: 800 }}>
            SMA&nbsp;MONITOR
          </Typography>
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
            {recomputingAll ? 'Recomputing…' : 'Recompute all'}
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
              onSaveThesis={onSaveThesis}
              onUpload={onUpload}
              onRecompute={onRecompute}
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
    </Box>
  )
}

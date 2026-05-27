import { useCallback, useEffect, useState } from 'react'
import AppBar from '@mui/material/AppBar'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Container from '@mui/material/Container'
import LinearProgress from '@mui/material/LinearProgress'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
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

  return (
    <Box sx={{ pb: 6 }}>
      <AppBar position="sticky" color="default" enableColorOnDark elevation={0}>
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ color: 'primary.main', fontWeight: 800 }}>
            SMA&nbsp;MONITOR
          </Typography>
          <Box sx={{ flexGrow: 1 }} />
          <StatusBar status={status} />
        </Toolbar>
        {!data && !error && <LinearProgress color="primary" />}
      </AppBar>

      <Container maxWidth="lg" sx={{ mt: 3 }}>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
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

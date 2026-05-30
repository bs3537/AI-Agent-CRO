import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import TrendingUpIcon from '@mui/icons-material/TrendingUp'
import TrendingDownIcon from '@mui/icons-material/TrendingDown'

// Open P&L with a green/red arrow and percentage. Renders a placeholder when
// cost basis is unknown.
export default function PnL({
  openPnl,
  pnlPct,
}: {
  openPnl: number | null
  pnlPct: number | null
}) {
  if (openPnl === null) {
    return (
      <Typography variant="body2" sx={{ opacity: 0.5 }}>
        Open P/L: no cost basis
      </Typography>
    )
  }
  const up = openPnl >= 0
  const color = up ? 'success.main' : 'error.main'
  const pct = pnlPct === null ? 'pct n/a' : `${pnlPct >= 0 ? '+' : ''}${(pnlPct * 100).toFixed(1)}%`
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, color }}>
      {up ? <TrendingUpIcon fontSize="small" /> : <TrendingDownIcon fontSize="small" />}
      <Typography variant="body1" sx={{ fontWeight: 700 }}>
        Open P/L {pct}
      </Typography>
    </Box>
  )
}

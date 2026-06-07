import Box from '@mui/material/Box'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import TrendingUpIcon from '@mui/icons-material/TrendingUp'
import TrendingDownIcon from '@mui/icons-material/TrendingDown'

// Open P&L with a green/red arrow and percentage. Renders a placeholder when
// cost basis is unknown. When `live` is true, a pulsing green dot indicates
// the figure is derived from an intraday FMP quote rather than EOD data.
export default function PnL({
  openPnl,
  pnlPct,
  live = false,
}: {
  openPnl: number | null
  pnlPct: number | null
  live?: boolean
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
      {live && (
        <Tooltip title="Intraday price (updates every 30 min during trading hours)">
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
        </Tooltip>
      )}
      {up ? <TrendingUpIcon fontSize="small" /> : <TrendingDownIcon fontSize="small" />}
      <Typography variant="body1" sx={{ fontWeight: 700 }}>
        Open P/L {pct}
      </Typography>
    </Box>
  )
}

import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Typography from '@mui/material/Typography'
import type { Status } from '../types'

// Compact operational readout for the app bar: positions count, today's spend
// vs budget, and the pull timestamp. Renders nothing until status loads.
export default function StatusBar({ status }: { status: Status | null }) {
  if (!status) return null
  const { spent_usd, budget_usd } = status.spend
  const pulled = status.positions.pulled_at
    ? new Date(status.positions.pulled_at).toLocaleString()
    : 'no pull yet'
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
      <Chip size="small" variant="outlined" label={`${status.positions.count} positions`} />
      <Chip
        size="small"
        variant="outlined"
        label={`$${spent_usd.toFixed(2)} / $${budget_usd.toFixed(0)} today`}
      />
      <Typography variant="caption" sx={{ opacity: 0.6 }}>
        positions pulled {pulled}
      </Typography>
    </Box>
  )
}

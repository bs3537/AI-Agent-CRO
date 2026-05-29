import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Typography from '@mui/material/Typography'
import type { Status } from '../types'

// Humanize a flag_name ("stale_positions" → "stale positions") for display.
function flagLabel(name: string): string {
  return name.replace(/_/g, ' ')
}

// Compact operational readout for the app bar: positions count, today's spend
// vs budget, any active operational flags (stale data, budget degraded, dead
// letters), and the pull timestamp. Renders nothing until status loads.
export default function StatusBar({ status }: { status: Status | null }) {
  if (!status) return null
  const { spent_usd, budget_usd } = status.spend
  const pulled = status.positions.pulled_at
    ? new Date(status.positions.pulled_at).toLocaleString()
    : 'no pull yet'
  // Active operational flags (the API already returns only active ones; we
  // re-check `active` defensively). Dead-letter flags read as errors, rest warn.
  const flags = (status.flags ?? []).filter((f) => Boolean(f.active))
  const deadPending = status.dead_letters?.pending ?? 0
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
      <Chip size="small" variant="outlined" label={`${status.positions.count} positions`} />
      <Chip
        size="small"
        variant="outlined"
        label={`$${spent_usd.toFixed(2)} / $${budget_usd.toFixed(0)} today`}
      />
      {flags.map((f) => {
        const name = String(f.flag_name ?? 'flag')
        return (
          <Chip
            key={name}
            size="small"
            color={name.includes('dead_letter') ? 'error' : 'warning'}
            label={flagLabel(name)}
          />
        )
      })}
      {deadPending > 0 && (
        <Chip
          size="small"
          color="error"
          label={`${deadPending} dead-letter${deadPending > 1 ? 's' : ''}`}
        />
      )}
      <Typography variant="caption" sx={{ opacity: 0.6 }}>
        positions pulled {pulled}
      </Typography>
    </Box>
  )
}

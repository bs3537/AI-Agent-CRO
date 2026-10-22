import Alert from '@mui/material/Alert'
import type { PositionSummary } from '../types'

// Warn when portfolio weights do not sum to ~100% or hand-added positions are
// no longer in the latest IBKR pull (likely closed positions to delete).
export default function WeightCheck({ positions }: { positions: PositionSummary[] }) {
  if (positions.length === 0) return null
  const total = positions.reduce((sum, p) => sum + p.pct_nav, 0) * 100
  const manual = positions.filter((p) => p.is_manual_only).map((p) => p.ticker)
  const off = Math.abs(total - 100) > 1.5
  if (!off && manual.length === 0) return null
  return (
    <Alert severity="warning" sx={{ mb: 2 }}>
      Weights add up to {total.toFixed(1)}% of NAV.
      {manual.length > 0 &&
        ` Hand-added positions not in the latest IBKR pull: ${manual.join(', ')}. Delete them if closed.`}
    </Alert>
  )
}

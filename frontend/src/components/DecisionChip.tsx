import Chip from '@mui/material/Chip'
import type { Decision } from '../types'
import { VERDICT_HEX } from '../theme'

// Colored HOLD/WATCH/SELL chip. Renders a neutral "no decision" chip when the
// position hasn't been computed yet.
export default function DecisionChip({ decision }: { decision: Decision | null }) {
  if (!decision) {
    return <Chip size="small" label="NO DECISION" variant="outlined" sx={{ opacity: 0.6 }} />
  }
  const hex = VERDICT_HEX[decision.color] ?? '#888'
  return (
    <Chip
      size="small"
      label={decision.verdict.toUpperCase()}
      sx={{
        bgcolor: hex,
        color: '#0E0E10',
        fontWeight: 700,
        letterSpacing: 0.5,
      }}
    />
  )
}

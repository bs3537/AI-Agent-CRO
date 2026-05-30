import Chip from '@mui/material/Chip'
import type { Decision, Rating } from '../types'
import { GRADE_HEX, VERDICT_HEX } from '../theme'

// Canonical synchronized rating chip. Falls back to the legacy decision shape
// during migration, then to a neutral empty state.
export default function DecisionChip({
  rating,
  decision,
}: {
  rating?: Rating | null
  decision: Decision | null
}) {
  if (rating) {
    const hex = GRADE_HEX[rating.grade] ?? '#888'
    return (
      <Chip
        size="small"
        label={`${rating.action.toUpperCase()} ${rating.grade}`}
        sx={{
          bgcolor: hex,
          color: '#0E0E10',
          fontWeight: 800,
          letterSpacing: 0.5,
        }}
      />
    )
  }
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

import Box from '@mui/material/Box'
import Tooltip from '@mui/material/Tooltip'
import type { Grade } from '../types'
import { GRADE_HEX } from '../theme'

// Plain-English meaning of each letter grade, shown on hover.
const GRADE_TITLE: Record<Grade, string> = {
  A: 'A — strong hold',
  B: 'B — hold',
  C: 'C — hold (on watch)',
  D: 'D — sell',
}

// Bold report-card letter grade for a position, colored by grade. Renders
// nothing when the position has no decision yet (so there is nothing to grade).
export default function GradeBadge({ grade }: { grade: Grade | null | undefined }) {
  if (!grade) return null
  const hex = GRADE_HEX[grade] ?? '#888'
  return (
    <Tooltip title={GRADE_TITLE[grade] ?? grade}>
      <Box
        sx={{
          width: 30,
          height: 30,
          borderRadius: 1.5,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          bgcolor: hex,
          color: '#0E0E10',
          fontWeight: 800,
          fontSize: 18,
          lineHeight: 1,
          flexShrink: 0,
        }}
      >
        {grade}
      </Box>
    </Tooltip>
  )
}

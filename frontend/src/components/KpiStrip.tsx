import { useMemo } from 'react'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Divider from '@mui/material/Divider'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import type { Grade, PositionSummary, QuoteInfo } from '../types'
import { GRADE_HEX } from '../theme'

const AMBER = '#F5B14C'
const BELOW_EMA_STATES = new Set(['below_ema20', 'extended_below_ema20'])
const GRADE_ORDER: Grade[] = ['A', 'B', 'C', 'D']

const valueSx = {
  fontVariantNumeric: 'tabular-nums',
  fontWeight: 600,
  fontSize: '1.05rem',
  lineHeight: 1.2,
} as const

function StatGroup({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <Box sx={{ flexShrink: 0 }}>
      <Typography
        sx={{
          fontSize: 11,
          textTransform: 'uppercase',
          letterSpacing: 0,
          opacity: 0.6,
          lineHeight: 1.6,
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </Typography>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          minHeight: 30,
        }}
      >
        {children}
      </Box>
    </Box>
  )
}

function catalystWithinSevenDays(pos: PositionSummary): boolean {
  if (
    pos.nearest_catalyst_days !== null &&
    pos.nearest_catalyst_days >= 0 &&
    pos.nearest_catalyst_days <= 7
  ) {
    return true
  }
  return pos.catalyst_outlook.some((item) => {
    if (!item.date) return false
    const timestamp = Date.parse(item.date)
    if (Number.isNaN(timestamp)) return false
    const days = (timestamp - Date.now()) / 86_400_000
    return days >= 0 && days <= 7
  })
}

export default function KpiStrip({
  positions,
  stale,
  liveQuotes,
  marketOpen,
}: {
  positions: PositionSummary[]
  stale: boolean
  liveQuotes: Record<string, QuoteInfo>
  marketOpen: boolean
}) {
  const stats = useMemo(() => {
    const grades: Record<Grade, number> = { A: 0, B: 0, C: 0, D: 0 }
    let unrated = 0
    let belowEma = 0
    let catalystSoon = 0

    for (const pos of positions) {
      const grade = pos.rating?.grade ?? pos.decision?.grade ?? null
      if (grade) grades[grade] += 1
      else unrated += 1

      const technical =
        pos.rating?.technical_state ?? pos.spark?.technical_state ?? null
      if (technical && BELOW_EMA_STATES.has(technical)) belowEma += 1
      if (catalystWithinSevenDays(pos)) catalystSoon += 1
    }

    return {
      grades,
      unrated,
      belowEma,
      catalystSoon,
      overdue: positions.some((pos) => pos.has_overdue_catalyst),
      liveCount: marketOpen
        ? positions.filter((pos) => Boolean(liveQuotes[pos.ticker])).length
        : 0,
    }
  }, [liveQuotes, marketOpen, positions])

  return (
    <Paper
      variant="outlined"
      sx={{ px: 2, py: 1.25, mb: 2, overflowX: 'auto', borderRadius: 1 }}
    >
      <Stack
        direction="row"
        spacing={2.5}
        alignItems="center"
        divider={<Divider orientation="vertical" flexItem />}
        sx={{ minWidth: 'min-content' }}
      >
        <StatGroup label="Grades">
          <Stack direction="row" spacing={0.5} alignItems="center">
            {GRADE_ORDER.map((grade) => (
              <Chip
                key={grade}
                size="small"
                variant="outlined"
                sx={{
                  borderColor: GRADE_HEX[grade],
                  opacity: stats.grades[grade] === 0 ? 0.4 : 1,
                  fontVariantNumeric: 'tabular-nums',
                }}
                label={
                  <Box component="span">
                    <Box
                      component="span"
                      sx={{ color: GRADE_HEX[grade], fontWeight: 800 }}
                    >
                      {grade}
                    </Box>{' '}
                    <Box component="span" sx={{ color: 'text.primary' }}>
                      {stats.grades[grade]}
                    </Box>
                  </Box>
                }
              />
            ))}
            {stats.unrated > 0 && (
              <Chip
                size="small"
                variant="outlined"
                sx={{ opacity: 0.5, fontVariantNumeric: 'tabular-nums' }}
                label={`unrated ${stats.unrated}`}
              />
            )}
          </Stack>
        </StatGroup>

        <StatGroup label="Below EMA">
          <Typography
            component="span"
            sx={{
              ...valueSx,
              color: stats.belowEma > 0 ? AMBER : 'text.primary',
            }}
          >
            {stats.belowEma}
          </Typography>
        </StatGroup>

        <StatGroup label="Catalyst <=7d">
          <Tooltip
            title={stats.overdue ? 'Includes overdue sidecar catalysts' : ''}
            disableHoverListener={!stats.overdue}
          >
            <Typography
              component="span"
              sx={{
                ...valueSx,
                color: stats.overdue ? 'error.main' : 'text.primary',
              }}
            >
              {stats.catalystSoon}
            </Typography>
          </Tooltip>
        </StatGroup>

        {stale && <Chip size="small" color="warning" label="STALE DATA" />}
      </Stack>
    </Paper>
  )
}

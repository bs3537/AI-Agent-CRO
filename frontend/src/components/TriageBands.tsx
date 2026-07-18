import { useMemo } from 'react'
import type { ElementType } from 'react'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Divider from '@mui/material/Divider'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import VisibilityIcon from '@mui/icons-material/Visibility'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import type { PositionSummary, QuoteInfo } from '../types'
import { signedPercent } from '../format'
import DecisionChip from './DecisionChip'

type Triage = 'attention' | 'watch'

const BAND_COLOR: Record<Triage, string> = {
  attention: '#FF5470',
  watch: '#F5B14C',
}

const BAND_META: Record<Triage, { icon: ElementType; title: string }> = {
  attention: { icon: WarningAmberIcon, title: 'Needs attention' },
  watch: { icon: VisibilityIcon, title: 'Watch' },
}

export function triageOf(pos: PositionSummary): Triage | null {
  const grade = pos.rating?.grade ?? pos.decision?.grade ?? null
  const attentionState = pos.rating?.attention_state
  const technical =
    pos.rating?.technical_state ?? pos.spark?.technical_state ?? null

  if (
    attentionState === 'broken' ||
    grade === 'D' ||
    pos.has_overdue_catalyst
  ) {
    return 'attention'
  }
  if (
    attentionState === 'watch' ||
    grade === 'C' ||
    technical === 'extended_below_ema20'
  ) {
    return 'watch'
  }
  return null
}

function pnlPct(pos: PositionSummary, quote: QuoteInfo | null): number | null {
  if (quote && pos.cost_basis) {
    return (quote.price * pos.qty - pos.cost_basis) / pos.cost_basis
  }
  return pos.pnl_pct
}

function BandRow({
  pos,
  quote,
  onOpenDetail,
}: {
  pos: PositionSummary
  quote: QuoteInfo | null
  onOpenDetail: (ticker: string) => void
}) {
  const gainLoss = pnlPct(pos, quote)
  const gainLossColor =
    gainLoss === null
      ? 'text.disabled'
      : gainLoss >= 0
        ? 'success.main'
        : 'error.main'
  const open = () => onOpenDetail(pos.ticker)
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          open()
        }
      }}
      sx={{
        minHeight: 42,
        py: 0.75,
        px: 1.5,
        cursor: 'pointer',
        '&:hover': { bgcolor: 'action.hover' },
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 700, flexShrink: 0 }}>
        {pos.ticker}
      </Typography>
      <Typography
        variant="body2"
        noWrap
        sx={{ color: 'text.secondary', flexShrink: 1, minWidth: 0 }}
      >
        {pos.company_name ?? '-'}
      </Typography>
      <Box sx={{ flexGrow: 1 }} />
      <Typography
        variant="body2"
        sx={{ fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}
      >
        {(pos.pct_nav * 100).toFixed(1)}%
      </Typography>
      <Typography
        variant="body2"
        sx={{
          fontVariantNumeric: 'tabular-nums',
          flexShrink: 0,
          minWidth: 58,
          textAlign: 'right',
          color: gainLossColor,
        }}
      >
        {gainLoss === null ? '-' : signedPercent(gainLoss)}
      </Typography>
      <DecisionChip rating={pos.rating} decision={pos.decision} />
      {pos.has_overdue_catalyst && (
        <Chip
          size="small"
          color="error"
          variant="outlined"
          label="overdue"
          sx={{ flexShrink: 0 }}
        />
      )}
    </Stack>
  )
}

function Band({
  triage,
  rows,
  liveQuotes,
  marketOpen,
  onOpenDetail,
}: {
  triage: Triage
  rows: PositionSummary[]
  liveQuotes: Record<string, QuoteInfo>
  marketOpen: boolean
  onOpenDetail: (ticker: string) => void
}) {
  const color = BAND_COLOR[triage]
  const { icon: Icon, title } = BAND_META[triage]
  return (
    <Paper
      variant="outlined"
      sx={{ mb: 1.25, borderLeft: `4px solid ${color}`, borderRadius: 1 }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          px: 1.5,
          py: 0.9,
        }}
      >
        <Icon fontSize="small" sx={{ color }} />
        <Typography
          variant="caption"
          sx={{
            textTransform: 'uppercase',
            letterSpacing: 0,
            fontWeight: 700,
            color,
          }}
        >
          {title} ({rows.length})
        </Typography>
      </Box>
      <Divider />
      <Stack divider={<Divider />}>
        {rows.map((pos) => (
          <BandRow
            key={pos.ticker}
            pos={pos}
            quote={marketOpen ? liveQuotes[pos.ticker] ?? null : null}
            onOpenDetail={onOpenDetail}
          />
        ))}
      </Stack>
    </Paper>
  )
}

export default function TriageBands({
  positions,
  liveQuotes,
  marketOpen,
  onOpenDetail,
}: {
  positions: PositionSummary[]
  liveQuotes: Record<string, QuoteInfo>
  marketOpen: boolean
  onOpenDetail: (ticker: string) => void
}) {
  const { attention, watch } = useMemo(() => {
    const attention: PositionSummary[] = []
    const watch: PositionSummary[] = []
    for (const pos of positions) {
      const triage = triageOf(pos)
      if (triage === 'attention') attention.push(pos)
      if (triage === 'watch') watch.push(pos)
    }
    attention.sort((left, right) => right.pct_nav - left.pct_nav)
    watch.sort((left, right) => right.pct_nav - left.pct_nav)
    return { attention, watch }
  }, [positions])

  if (attention.length === 0 && watch.length === 0) {
    return (
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="center"
        sx={{ color: 'success.main', mb: 2, opacity: 0.8 }}
      >
        <CheckCircleOutlineIcon fontSize="small" />
        <Typography variant="body2">Nothing needs attention</Typography>
      </Stack>
    )
  }

  return (
    <Box sx={{ mb: 2 }}>
      {attention.length > 0 && (
        <Band
          triage="attention"
          rows={attention}
          liveQuotes={liveQuotes}
          marketOpen={marketOpen}
          onOpenDetail={onOpenDetail}
        />
      )}
      {watch.length > 0 && (
        <Band
          triage="watch"
          rows={watch}
          liveQuotes={liveQuotes}
          marketOpen={marketOpen}
          onOpenDetail={onOpenDetail}
        />
      )}
    </Box>
  )
}

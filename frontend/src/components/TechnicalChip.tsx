import Chip from '@mui/material/Chip'
import type { PositionSummary } from '../types'

export const TECHNICAL_LABELS: Record<string, string> = {
  above_ema20: 'above 20-EMA',
  below_ema20: 'below 20-EMA',
  extended_below_ema20: 'extended below 20-EMA',
  no_price_data: 'no price data',
}

export function technicalColor(state: string): 'success' | 'default' | 'warning' {
  return state === 'above_ema20'
    ? 'success'
    : state === 'no_price_data'
      ? 'default'
      : 'warning'
}

export function TechnicalChip({ pos }: { pos: PositionSummary }) {
  const state = pos.rating?.technical_state ?? pos.spark?.technical_state ?? 'no_price_data'
  const pct = pos.rating?.price_vs_ema20_pct ?? pos.spark?.price_vs_ema20_pct
  const stateLabel = TECHNICAL_LABELS[state] ?? state
  const label =
    pct !== null && pct !== undefined && state !== 'no_price_data'
      ? `${stateLabel} ${(pct * 100).toFixed(1)}%`
      : stateLabel
  return (
    <Chip
      size="small"
      color={technicalColor(state)}
      variant="outlined"
      label={label}
    />
  )
}

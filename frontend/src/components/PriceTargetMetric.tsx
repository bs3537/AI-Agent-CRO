import Box from '@mui/material/Box'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import type { AnalystTarget } from '../types'

// Compact TipRanks target metric for the position-card economics row.
export default function PriceTargetMetric({ target }: { target: AnalystTarget | null }) {
  const hasTarget = target?.mean_price_target != null
  const isStale = target?.status === 'stale'
  const upside = target?.upside_pct
  const upsideColor = upside == null
    ? 'text.secondary'
    : upside >= 0 ? 'success.main' : 'error.main'
  const targetValue = hasTarget
    ? formatCurrency(target.mean_price_target as number, target.currency)
    : '—'
  const upsideValue = upside == null
    ? !target ? 'Unavailable'
      : target.status === 'unavailable'
        ? target.unavailable_reason === 'no_analyst_coverage' ? 'No coverage' : 'Unavailable'
        : 'Pending EOD'
    : `${upside >= 0 ? '+' : ''}${(upside * 100).toFixed(1)}%`
  const tooltip = targetTooltip(target)

  const metric = (
    <Box
      component={target?.source_url ? 'a' : 'div'}
      href={target?.source_url || undefined}
      target={target?.source_url ? '_blank' : undefined}
      rel={target?.source_url ? 'noreferrer' : undefined}
      sx={{
        width: 190,
        minHeight: 40,
        px: 1,
        py: 0.5,
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        alignItems: 'center',
        border: '1px solid',
        borderColor: isStale ? 'warning.main' : 'divider',
        borderRadius: 1,
        color: 'text.primary',
        textDecoration: 'none',
        bgcolor: 'rgba(255,255,255,0.018)',
        transition: 'border-color 120ms ease, background-color 120ms ease',
        '&:hover': target?.source_url ? {
          borderColor: isStale ? 'warning.light' : 'rgba(255,106,0,0.58)',
          bgcolor: 'rgba(255,106,0,0.045)',
        } : undefined,
      }}
    >
      <Box sx={{ minWidth: 0 }}>
        <Typography
          component="div"
          sx={{ fontSize: 9, lineHeight: 1.1, color: 'text.secondary', fontWeight: 700 }}
        >
          PRICE TARGET · TIPRANKS
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.75, mt: 0.2 }}>
          <Typography component="span" sx={{ fontSize: 14, lineHeight: 1.2, fontWeight: 750 }}>
            {targetValue}
          </Typography>
          <Typography
            component="span"
            sx={{
              minWidth: 58,
              fontSize: upside == null ? 10 : 12,
              lineHeight: 1.2,
              fontWeight: 700,
              color: upsideColor,
              whiteSpace: 'nowrap',
            }}
          >
            {upsideValue}
          </Typography>
        </Box>
      </Box>
      {isStale ? (
        <WarningAmberIcon sx={{ fontSize: 15, color: 'warning.main', ml: 0.5 }} />
      ) : (
        target?.source_url && (
          <OpenInNewIcon sx={{ fontSize: 14, color: 'text.secondary', ml: 0.5 }} />
        )
      )}
    </Box>
  )

  return <Tooltip title={tooltip} arrow>{metric}</Tooltip>
}

// Build the attribution and freshness tooltip for each target state.
function targetTooltip(target: AnalystTarget | null) {
  if (!target) {
    return 'TipRanks analyst target has not been collected yet.'
  }
  if (!target.mean_price_target) {
    return target.unavailable_reason === 'no_analyst_coverage'
      ? 'No TipRanks sell-side analyst consensus target is currently available.'
      : 'The TipRanks target is temporarily unavailable after a failed refresh.'
  }
  const parts = [
    target.analyst_count != null
      ? `${target.analyst_count} Wall Street analyst${target.analyst_count === 1 ? '' : 's'}`
      : 'Wall Street analyst consensus',
    rangeLabel(target),
    target.target_fetched_at
      ? `Target refreshed ${formatDate(target.target_fetched_at)}`
      : null,
    target.reference_close != null && target.price_as_of
      ? `Upside vs ${formatCurrency(target.reference_close, target.currency)} close on ${formatDate(target.price_as_of)}`
      : 'Waiting for a dated EOD reference close',
    target.status === 'stale'
      ? 'Last successful target retained because the latest refresh failed or is overdue'
      : null,
  ]
  return parts.filter(Boolean).join(' · ')
}

// Format the TipRanks high/low range when both values are present.
function rangeLabel(target: AnalystTarget): string | null {
  if (target.low_price_target == null || target.high_price_target == null) return null
  return `Range ${formatCurrency(target.low_price_target, target.currency)}–${formatCurrency(target.high_price_target, target.currency)}`
}

// Format target values in their reported currency with stable compact precision.
function formatCurrency(value: number, currency: string | null): string {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
      minimumFractionDigits: value >= 100 ? 0 : 2,
      maximumFractionDigits: value >= 100 ? 2 : 2,
    }).format(value)
  } catch {
    return `$${value.toFixed(2)}`
  }
}

// Format ISO timestamps and trading dates for concise local display.
function formatDate(value: string): string {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T12:00:00Z`)
    : new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

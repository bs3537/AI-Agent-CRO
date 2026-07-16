import Box from '@mui/material/Box'
import Link from '@mui/material/Link'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import InsightsOutlinedIcon from '@mui/icons-material/InsightsOutlined'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import type { AnalystTarget } from '../types'

export default function ThesisTargetLine({
  target,
  isEtf,
}: {
  target: AnalystTarget | null
  isEtf: boolean
}) {
  if (isEtf) return null

  const mean = target?.mean_price_target
  const upside = target?.upside_pct
  const hasTarget = mean != null
  const label = hasTarget
    ? `${formatCurrency(mean, target?.currency)} (${upside == null ? 'upside pending' : formatUpside(upside)})`
    : target?.unavailable_reason === 'no_analyst_coverage'
      ? 'No current analyst coverage'
      : 'Pending weekly refresh'
  const analystLabel = target?.analyst_count != null
    ? `${target.analyst_count} analyst${target.analyst_count === 1 ? '' : 's'}`
    : null
  const color = upside == null
    ? 'text.secondary'
    : upside >= 0 ? 'success.main' : 'error.main'

  const content = (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.65, minWidth: 0 }}>
      <InsightsOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary', flexShrink: 0 }} />
      <Typography variant="caption" sx={{ color: 'text.secondary', flexShrink: 0 }}>
        Price target
      </Typography>
      <Typography variant="body2" sx={{ color, fontWeight: 700 }}>
        {label}
      </Typography>
      {analystLabel && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          · TipRanks mean · {analystLabel}
        </Typography>
      )}
      {target?.source_url && (
        <OpenInNewIcon sx={{ fontSize: 13, color: 'text.secondary', flexShrink: 0 }} />
      )}
    </Box>
  )

  return (
    <Tooltip title={targetTooltip(target)} arrow>
      {target?.source_url ? (
        <Link
          href={target.source_url}
          target="_blank"
          rel="noreferrer"
          underline="none"
          sx={{ display: 'inline-flex', maxWidth: '100%' }}
        >
          {content}
        </Link>
      ) : content}
    </Tooltip>
  )
}

function formatUpside(value: number): string {
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}% upside`
}

function formatCurrency(value: number, currency: string | null | undefined): string {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
      minimumFractionDigits: value >= 100 ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(value)
  } catch {
    return `$${value.toFixed(2)}`
  }
}

function targetTooltip(target: AnalystTarget | null): string {
  if (!target) return 'TipRanks consensus has not been collected yet.'
  if (target.status === 'stale') {
    return 'Last successful TipRanks mean target retained; the latest weekly refresh failed or is overdue.'
  }
  if (target.mean_price_target == null) {
    return target.unavailable_reason === 'no_analyst_coverage'
      ? 'TipRanks currently reports no sell-side consensus target.'
      : 'TipRanks consensus is temporarily unavailable.'
  }
  return target.price_as_of
    ? `Upside uses the dated end-of-day close from ${target.price_as_of}.`
    : 'The target is current; upside is waiting for a dated end-of-day close.'
}

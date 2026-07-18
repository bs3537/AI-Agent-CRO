import { useMemo, useState } from 'react'
import Box from '@mui/material/Box'
import IconButton from '@mui/material/IconButton'
import Link from '@mui/material/Link'
import Paper from '@mui/material/Paper'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import TableSortLabel from '@mui/material/TableSortLabel'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import type {
  CatalystOutlookItem,
  PositionSummary,
  QuoteInfo,
} from '../types'
import { signedPercent } from '../format'
import DecisionChip from './DecisionChip'
import Sparkline from './Sparkline'

const AMBER = '#F5B14C'
const PRIMARY = '#FF6A00'
const PRICE_BLUE = '#2F80FF'

const CATALYST_TAG: Record<
  CatalystOutlookItem['type'],
  { text: string; color: string }
> = {
  regulatory: { text: 'REG', color: PRIMARY },
  clinical_data: { text: 'DATA', color: PRICE_BLUE },
  product_launch: { text: 'LAUNCH', color: '#39D98A' },
  earnings: { text: 'ER', color: 'text.secondary' },
  investor_event: { text: 'EVENT', color: 'text.secondary' },
  contract_milestone: { text: 'MILESTONE', color: AMBER },
  fund_event: { text: 'FUND', color: '#A3D977' },
  other: { text: 'OTHER', color: 'text.secondary' },
}

type SortKey =
  | 'symbol'
  | 'weight'
  | 'gl'
  | 'upside'
  | 'pt'
  | 'ema'
  | 'catalyst'
  | 'rating'
type SortState = { key: SortKey; dir: 'asc' | 'desc' }

const DEFAULT_DESC = new Set<SortKey>(['weight', 'gl', 'upside', 'pt', 'ema'])
const GRADE_RANK: Record<string, number> = { A: 0, B: 1, C: 2, D: 3 }

function targetMean(pos: PositionSummary): number | null {
  if (pos.is_etf || !pos.analyst_target) return null
  if (!['current', 'stale'].includes(pos.analyst_target.status)) return null
  return pos.analyst_target.mean_price_target
}

function targetUpside(pos: PositionSummary): number | null {
  return targetMean(pos) === null ? null : pos.analyst_target?.upside_pct ?? null
}

function displayPnlPct(
  pos: PositionSummary,
  quote: QuoteInfo | null,
): number | null {
  if (quote && pos.cost_basis) {
    return (quote.price * pos.qty - pos.cost_basis) / pos.cost_basis
  }
  return pos.pnl_pct
}

function signColor(value: number | null): string | undefined {
  if (value === null) return undefined
  return value >= 0 ? 'success.main' : 'error.main'
}

function emaColor(state: string | null | undefined): string | undefined {
  if (state === 'above_ema20') return 'success.main'
  if (state === 'below_ema20' || state === 'extended_below_ema20') return AMBER
  return undefined
}

function daysUntil(dateString: string | null): number | null {
  if (!dateString) return null
  const timestamp = Date.parse(dateString)
  if (Number.isNaN(timestamp)) return null
  return (timestamp - Date.now()) / 86_400_000
}

function soonestCatalyst(pos: PositionSummary): number | null {
  const timestamps = pos.catalyst_outlook
    .map((item) => (item.date ? Date.parse(item.date) : Number.NaN))
    .filter((timestamp) => !Number.isNaN(timestamp))
  return timestamps.length > 0 ? Math.min(...timestamps) : null
}

function ratingRank(pos: PositionSummary): number | null {
  const grade = pos.rating?.grade ?? pos.decision?.grade ?? null
  if (!grade) return null
  const action =
    pos.rating?.action ?? (pos.decision?.verdict === 'sell' ? 'sell' : 'hold')
  return (GRADE_RANK[grade] ?? 3) * 2 + (action === 'sell' ? 1 : 0)
}

function sortValue(
  pos: PositionSummary,
  key: SortKey,
  quote: QuoteInfo | null,
): number | string | null {
  switch (key) {
    case 'symbol':
      return pos.ticker
    case 'weight':
      return pos.pct_nav
    case 'gl':
      return displayPnlPct(pos, quote)
    case 'upside':
      return targetUpside(pos)
    case 'pt':
      return targetMean(pos)
    case 'ema':
      return (
        pos.rating?.price_vs_ema20_pct ??
        pos.spark?.price_vs_ema20_pct ??
        null
      )
    case 'catalyst':
      return soonestCatalyst(pos)
    case 'rating':
      return ratingRank(pos)
  }
}

function compareValues(
  left: number | string | null,
  right: number | string | null,
  direction: 'asc' | 'desc',
): number {
  if (left === null && right === null) return 0
  if (left === null) return 1
  if (right === null) return -1
  const comparison =
    typeof left === 'number' && typeof right === 'number'
      ? left - right
      : String(left).localeCompare(String(right))
  return direction === 'asc' ? comparison : -comparison
}

const headCellSx = {
  bgcolor: 'background.paper',
  borderBottom: '1px solid',
  borderColor: 'divider',
  color: 'text.secondary',
  fontWeight: 600,
  fontSize: 12,
  letterSpacing: 0,
  whiteSpace: 'nowrap',
} as const

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  align = 'left',
}: {
  label: string
  sortKey: SortKey
  sort: SortState
  onSort: (key: SortKey) => void
  align?: 'left' | 'right'
}) {
  return (
    <TableCell
      align={align}
      sortDirection={sort.key === sortKey ? sort.dir : false}
      sx={headCellSx}
    >
      <TableSortLabel
        active={sort.key === sortKey}
        direction={sort.key === sortKey ? sort.dir : 'asc'}
        onClick={() => onSort(sortKey)}
      >
        {label}
      </TableSortLabel>
    </TableCell>
  )
}

function NumericCell({
  value,
  format,
  color,
  tooltip,
}: {
  value: number | null
  format: (value: number) => string
  color?: string
  tooltip?: string
}) {
  const content = value === null ? '-' : format(value)
  return (
    <TableCell
      align="right"
      sx={{
        fontVariantNumeric: 'tabular-nums',
        whiteSpace: 'nowrap',
        color: value === null ? 'text.disabled' : color,
      }}
    >
      {tooltip ? <Tooltip title={tooltip}><span>{content}</span></Tooltip> : content}
    </TableCell>
  )
}

function CatalystCell({ items }: { items: CatalystOutlookItem[] }) {
  if (items.length === 0) {
    return <TableCell sx={{ color: 'text.disabled' }}>-</TableCell>
  }
  return (
    <TableCell sx={{ py: 0.5, minWidth: 190 }}>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.35 }}>
        {items.slice(0, 3).map((item, index) => {
          const tag = CATALYST_TAG[item.type] ?? CATALYST_TAG.other
          const days = daysUntil(item.date)
          const imminent = days !== null && days >= 0 && days <= 30
          return (
            <Tooltip
              key={`${item.source_url}-${index}`}
              title={`${item.label} | ${item.source_title}${item.confirmed ? '' : ' | estimated window'}`}
            >
              <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.65 }}>
                <Typography
                  component="span"
                  variant="caption"
                  sx={{
                    width: 58,
                    flexShrink: 0,
                    color: tag.color,
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: 0,
                  }}
                >
                  {tag.text}
                </Typography>
                <Link
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  underline="hover"
                  onClick={(event) => event.stopPropagation()}
                  sx={{
                    color: imminent ? AMBER : 'text.primary',
                    fontSize: 11,
                    lineHeight: 1.4,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {item.confirmed ? '' : '~'}{item.date_label}
                </Link>
              </Box>
            </Tooltip>
          )
        })}
      </Box>
    </TableCell>
  )
}

export default function PositionsTable({
  positions,
  stale,
  liveQuotes,
  marketOpen,
  onOpenDetail,
}: {
  positions: PositionSummary[]
  stale: boolean
  liveQuotes: Record<string, QuoteInfo>
  marketOpen: boolean
  onOpenDetail: (ticker: string) => void
}) {
  const [sort, setSort] = useState<SortState>({ key: 'weight', dir: 'desc' })

  const onSort = (key: SortKey) => {
    setSort((previous) =>
      previous.key === key
        ? { key, dir: previous.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: DEFAULT_DESC.has(key) ? 'desc' : 'asc' },
    )
  }

  const rows = useMemo(() => {
    const copy = [...positions]
    copy.sort((left, right) =>
      compareValues(
        sortValue(left, sort.key, marketOpen ? liveQuotes[left.ticker] ?? null : null),
        sortValue(right, sort.key, marketOpen ? liveQuotes[right.ticker] ?? null : null),
        sort.dir,
      ),
    )
    return copy
  }, [liveQuotes, marketOpen, positions, sort])

  return (
    <TableContainer
      component={Paper}
      variant="outlined"
      sx={{ maxHeight: 'calc(100vh - 240px)', borderRadius: 1 }}
    >
      <Table
        stickyHeader
        size="small"
        sx={{ minWidth: 1180, '& tbody td': { verticalAlign: 'middle' } }}
      >
        <TableHead>
          <TableRow>
            <SortHeader label="Symbol" sortKey="symbol" sort={sort} onSort={onSort} />
            <TableCell sx={headCellSx}>Company</TableCell>
            <SortHeader label="Weight" sortKey="weight" sort={sort} onSort={onSort} align="right" />
            <SortHeader label="G/L" sortKey="gl" sort={sort} onSort={onSort} align="right" />
            <SortHeader label="PT upside" sortKey="upside" sort={sort} onSort={onSort} align="right" />
            <SortHeader label="Mean PT" sortKey="pt" sort={sort} onSort={onSort} align="right" />
            <TableCell sx={headCellSx}>Trend</TableCell>
            <SortHeader label="vs 20-EMA" sortKey="ema" sort={sort} onSort={onSort} align="right" />
            <TableCell sx={headCellSx}>Upcoming catalysts</TableCell>
            <SortHeader label="Rating" sortKey="rating" sort={sort} onSort={onSort} />
            <TableCell align="right" sx={headCellSx} />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((pos) => {
            const quote = marketOpen ? liveQuotes[pos.ticker] ?? null : null
            const pnlPct = displayPnlPct(pos, quote)
            const target = pos.analyst_target
            const meanTarget = targetMean(pos)
            const targetTooltip =
              meanTarget === null
                ? undefined
                : `${target?.analyst_count ?? 'Unspecified'} analysts | FMP consensus${target?.target_fetched_at ? ` | fetched ${new Date(target.target_fetched_at).toLocaleDateString()}` : ''}`
            const thesisLabel = pos.thesis.trim().toUpperCase()
            const isStub =
              pos.thesis_source === 'system_stub' ||
              thesisLabel.startsWith('STUB') ||
              thesisLabel.startsWith('PLACEHOLDER')
            const warnReasons: string[] = []
            if (stale) warnReasons.push('Stale portfolio data')
            if (isStub) warnReasons.push('Thesis still requires PM review')
            const emaState =
              pos.rating?.technical_state ?? pos.spark?.technical_state ?? null
            const emaPct =
              pos.rating?.price_vs_ema20_pct ??
              pos.spark?.price_vs_ema20_pct ??
              null

            return (
              <TableRow
                key={pos.ticker}
                hover
                tabIndex={0}
                sx={{ cursor: 'pointer' }}
                onClick={() => onOpenDetail(pos.ticker)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onOpenDetail(pos.ticker)
                  }
                }}
              >
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>
                      {pos.ticker}
                    </Typography>
                    {quote && (
                      <Tooltip title="Intraday FMP quote">
                        <Typography component="span" sx={{ color: 'success.main', fontSize: 10 }}>
                          LIVE
                        </Typography>
                      </Tooltip>
                    )}
                    {warnReasons.length > 0 && (
                      <Tooltip title={warnReasons.join(' | ')}>
                        <Box
                          sx={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            bgcolor: AMBER,
                            flexShrink: 0,
                          }}
                        />
                      </Tooltip>
                    )}
                  </Box>
                </TableCell>
                <TableCell>
                  <Tooltip title={pos.company_name ?? ''}>
                    <Typography
                      variant="body2"
                      noWrap
                      sx={{ maxWidth: 180, color: 'text.secondary' }}
                    >
                      {pos.company_name ?? '-'}
                    </Typography>
                  </Tooltip>
                </TableCell>
                <NumericCell value={pos.pct_nav} format={(value) => `${(value * 100).toFixed(1)}%`} />
                <NumericCell value={pnlPct} format={signedPercent} color={signColor(pnlPct)} />
                <NumericCell
                  value={targetUpside(pos)}
                  format={signedPercent}
                  color={signColor(targetUpside(pos))}
                  tooltip={
                    target?.price_as_of
                      ? `EOD upside using the ${target.price_as_of} reference close`
                      : undefined
                  }
                />
                <NumericCell
                  value={meanTarget}
                  format={(value) => `$${value.toFixed(2)}`}
                  tooltip={targetTooltip}
                />
                <TableCell sx={{ py: 0.5 }}>
                  <Sparkline spark={pos.spark} width={110} height={28} />
                </TableCell>
                <NumericCell
                  value={emaPct}
                  format={signedPercent}
                  color={emaColor(emaState)}
                />
                <CatalystCell items={pos.catalyst_outlook} />
                <TableCell>
                  <DecisionChip rating={pos.rating} decision={pos.decision} />
                </TableCell>
                <TableCell align="right">
                  <Tooltip title={`Open ${pos.ticker} details`}>
                    <IconButton
                      size="small"
                      aria-label={`Open ${pos.ticker} details`}
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpenDetail(pos.ticker)
                      }}
                    >
                      <InfoOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

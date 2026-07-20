import { useCallback, useEffect, useMemo, useState } from 'react'
import Alert from '@mui/material/Alert'
import AppBar from '@mui/material/AppBar'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import Container from '@mui/material/Container'
import IconButton from '@mui/material/IconButton'
import LinearProgress from '@mui/material/LinearProgress'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Tab from '@mui/material/Tab'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Tabs from '@mui/material/Tabs'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import Toolbar from '@mui/material/Toolbar'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import RefreshIcon from '@mui/icons-material/Refresh'
import TrendingDownIcon from '@mui/icons-material/TrendingDown'
import TrendingUpIcon from '@mui/icons-material/TrendingUp'
import { api } from '../api'
import { compactUsd } from '../format'
import type {
  HealthcareMover,
  HealthcareMoversResponse,
} from '../types'
import BrandLogo from './BrandLogo'
import MoverSparkline from './MoverSparkline'

const WINDOWS = [1, 2, 3, 4, 5] as const
const REFRESH_MS = 5 * 60 * 1000
const GAIN = '#39D98A'
const LOSS = '#FF5470'

const FLAG_LABELS: Record<string, string> = {
  under_one_dollar: 'Under $1',
  low_liquidity: 'Low liquidity',
  new_or_incomplete_history: 'Limited history',
  volume_spike: 'Volume spike',
}

function formatMove(value: number): string {
  return `${value >= 0 ? '+' : '-'}${Math.abs(value).toFixed(1)}%`
}

function formatVolume(value: number | null): string {
  if (value === null) return '-'
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`
  return value.toLocaleString()
}

function formatDate(value: string | null): string {
  if (!value) return '-'
  return new Date(`${value}T12:00:00Z`).toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatTimestamp(value: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function tabRefreshStamp(value: string | null): { date: string; time: string } {
  if (!value) return { date: 'No refresh', time: '-' }
  const timestamp = new Date(value)
  const timeParts = new Intl.DateTimeFormat([], {
    timeZone: 'America/New_York',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).formatToParts(timestamp)
  const part = (type: string) =>
    timeParts.find((candidate) => candidate.type === type)?.value ?? ''
  const dayPeriod = part('dayPeriod').slice(0, 1).toLowerCase()
  return {
    date: timestamp.toLocaleDateString([], {
      timeZone: 'America/New_York',
      month: 'short',
      day: 'numeric',
    }),
    time: `${part('hour')}:${part('minute')}${dayPeriod} ET`,
  }
}

function QualityFlags({ row }: { row: HealthcareMover }) {
  if (row.flags.length === 0) {
    return <Typography variant="caption" color="text.disabled">-</Typography>
  }
  return (
    <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
      {row.flags.map((flag) => (
        <Chip
          key={flag}
          size="small"
          label={FLAG_LABELS[flag] ?? flag.split('_').join(' ')}
          color={flag === 'volume_spike' ? 'success' : 'default'}
          variant="outlined"
          sx={{ height: 20, fontSize: 10, borderRadius: '5px' }}
        />
      ))}
    </Stack>
  )
}

function Identity({ row }: { row: HealthcareMover }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Typography sx={{ fontWeight: 800, fontSize: 14, color: 'text.primary' }}>
          {row.ticker}
        </Typography>
        {row.is_held && (
          <Chip
            label="HELD"
            size="small"
            color="primary"
            variant="outlined"
            sx={{ height: 18, fontSize: 9, fontWeight: 800, borderRadius: '4px' }}
          />
        )}
      </Stack>
      <Tooltip title={row.company_name}>
        <Typography
          noWrap
          sx={{ color: 'text.secondary', fontSize: 12, maxWidth: 260 }}
        >
          {row.company_name}
        </Typography>
      </Tooltip>
    </Box>
  )
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string
  value: string
  accent?: string
}) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography
        sx={{ color: 'text.secondary', fontSize: 10, fontWeight: 700, textTransform: 'uppercase' }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          color: accent ?? 'text.primary',
          fontSize: { xs: 15, sm: 17 },
          fontWeight: 750,
          fontVariantNumeric: 'tabular-nums',
          whiteSpace: 'nowrap',
        }}
      >
        {value}
      </Typography>
    </Box>
  )
}

function DesktopTable({
  rows,
  direction,
}: {
  rows: HealthcareMover[]
  direction: 'gainers' | 'decliners'
}) {
  const color = direction === 'gainers' ? GAIN : LOSS
  const headerSx = {
    bgcolor: '#17171A',
    color: 'text.secondary',
    fontSize: 11,
    fontWeight: 700,
    whiteSpace: 'nowrap',
    borderColor: 'divider',
  } as const
  return (
    <TableContainer
      component={Paper}
      variant="outlined"
      sx={{ display: { xs: 'none', md: 'block' }, borderRadius: '8px' }}
    >
      <Table stickyHeader size="small" sx={{ minWidth: 1060 }}>
        <TableHead>
          <TableRow>
            <TableCell align="center" sx={{ ...headerSx, width: 52 }}>Rank</TableCell>
            <TableCell sx={headerSx}>Company</TableCell>
            <TableCell align="right" sx={headerSx}>Change</TableCell>
            <TableCell align="right" sx={headerSx}>Price</TableCell>
            <TableCell sx={headerSx}>Trend</TableCell>
            <TableCell align="right" sx={headerSx}>Market cap</TableCell>
            <TableCell align="right" sx={headerSx}>Volume</TableCell>
            <TableCell align="right" sx={headerSx}>vs 20D volume</TableCell>
            <TableCell sx={headerSx}>Industry</TableCell>
            <TableCell sx={headerSx}>Flags</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.ticker} hover>
              <TableCell
                align="center"
                sx={{ fontWeight: 800, color: 'text.secondary', fontVariantNumeric: 'tabular-nums' }}
              >
                {row.rank}
              </TableCell>
              <TableCell sx={{ py: 1.1 }}><Identity row={row} /></TableCell>
              <TableCell
                align="right"
                sx={{ color, fontWeight: 850, fontSize: 17, fontVariantNumeric: 'tabular-nums' }}
              >
                {formatMove(row.return_pct)}
              </TableCell>
              <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                ${row.price.toFixed(row.price < 10 ? 2 : 1)}
              </TableCell>
              <TableCell>
                <MoverSparkline
                  closes={row.spark_closes}
                  positive={direction === 'gainers'}
                />
              </TableCell>
              <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                {row.market_cap === null ? '-' : compactUsd(row.market_cap)}
              </TableCell>
              <TableCell align="right">{formatVolume(row.latest_volume)}</TableCell>
              <TableCell
                align="right"
                sx={{
                  color: row.volume_ratio !== null && row.volume_ratio >= 3 ? GAIN : 'text.primary',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {row.volume_ratio === null ? '-' : `${row.volume_ratio.toFixed(1)}x`}
              </TableCell>
              <TableCell>
                <Typography noWrap sx={{ maxWidth: 165, fontSize: 12, color: 'text.secondary' }}>
                  {row.industry ?? '-'}
                </Typography>
              </TableCell>
              <TableCell sx={{ minWidth: 150 }}><QualityFlags row={row} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function MobileList({
  rows,
  direction,
}: {
  rows: HealthcareMover[]
  direction: 'gainers' | 'decliners'
}) {
  const color = direction === 'gainers' ? GAIN : LOSS
  return (
    <Stack spacing={1} sx={{ display: { xs: 'flex', md: 'none' } }}>
      {rows.map((row) => (
        <Paper
          key={row.ticker}
          variant="outlined"
          sx={{ p: 1.5, borderRadius: '8px', overflow: 'hidden' }}
        >
          <Stack direction="row" alignItems="flex-start" spacing={1.25}>
            <Typography
              sx={{
                width: 24,
                color: 'text.secondary',
                fontWeight: 800,
                fontVariantNumeric: 'tabular-nums',
                flexShrink: 0,
              }}
            >
              {row.rank}
            </Typography>
            <Box sx={{ minWidth: 0, flexGrow: 1 }}>
              <Identity row={row} />
              <Typography noWrap sx={{ color: 'text.disabled', fontSize: 11, mt: 0.25 }}>
                {row.industry ?? 'Healthcare'} · {row.exchange ?? 'US'}
              </Typography>
            </Box>
            <Box sx={{ textAlign: 'right', flexShrink: 0 }}>
              <Typography
                sx={{ color, fontSize: 18, fontWeight: 850, fontVariantNumeric: 'tabular-nums' }}
              >
                {formatMove(row.return_pct)}
              </Typography>
              <Typography sx={{ color: 'text.secondary', fontSize: 11 }}>
                ${row.price.toFixed(row.price < 10 ? 2 : 1)}
              </Typography>
            </Box>
          </Stack>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: '1fr auto',
              alignItems: 'end',
              gap: 1,
              mt: 1.25,
              pt: 1,
              borderTop: '1px solid',
              borderColor: 'divider',
            }}
          >
            <Stack direction="row" spacing={2}>
              <Stat
                label="Market cap"
                value={row.market_cap === null ? '-' : compactUsd(row.market_cap)}
              />
              <Stat label="Volume" value={formatVolume(row.latest_volume)} />
              <Stat
                label="Activity"
                value={row.volume_ratio === null ? '-' : `${row.volume_ratio.toFixed(1)}x`}
              />
            </Stack>
            <MoverSparkline
              closes={row.spark_closes}
              positive={direction === 'gainers'}
              width={84}
              height={30}
            />
          </Box>
          {row.flags.length > 0 && <Box sx={{ mt: 1 }}><QualityFlags row={row} /></Box>}
        </Paper>
      ))}
    </Stack>
  )
}

export default function HealthcareMoversPage({
  onNavigatePortfolio,
}: {
  onNavigatePortfolio: () => void
}) {
  const [data, setData] = useState<HealthcareMoversResponse | null>(null)
  const [windowDays, setWindowDays] = useState(5)
  const [direction, setDirection] = useState<'gainers' | 'decliners'>('gainers')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.healthcareMovers())
      setError(null)
    } catch (caught) {
      setError(String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  const rows = data?.rankings[String(windowDays)]?.[direction] ?? []
  const largestMove = rows[0]?.return_pct ?? null
  const medianMove = useMemo(() => {
    if (rows.length === 0) return null
    const sorted = rows.map((row) => row.return_pct).sort((a, b) => a - b)
    return sorted[Math.floor(sorted.length / 2)]
  }, [rows])
  const accent = direction === 'gainers' ? GAIN : LOSS
  const refreshStamp = tabRefreshStamp(
    data?.status === 'current' ? data.generated_at : null,
  )

  return (
    <Box sx={{ pb: 6, minHeight: '100vh' }}>
      <AppBar position="sticky" color="default" enableColorOnDark elevation={0}>
        <Toolbar sx={{ gap: 1.5, minHeight: { xs: 64, sm: 68 } }}>
          <BrandLogo />
          <Box sx={{ flexGrow: 1 }} />
          <Tooltip title="Portfolio dashboard">
            <Button
              color="inherit"
              variant="outlined"
              size="small"
              startIcon={<ArrowBackIcon />}
              onClick={onNavigatePortfolio}
              sx={{
                whiteSpace: 'nowrap',
                minWidth: { xs: 36, sm: 'auto' },
                px: { xs: 1, sm: 1.5 },
                '& .MuiButton-startIcon': { mr: { xs: 0, sm: 1 } },
              }}
            >
              <Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>
                Portfolio
              </Box>
            </Button>
          </Tooltip>
        </Toolbar>
        {loading && <LinearProgress color="primary" />}
      </AppBar>

      <Container maxWidth={false} sx={{ mt: { xs: 2, sm: 3 }, px: { xs: 1.5, sm: 3 } }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          justifyContent="space-between"
          spacing={2}
          sx={{ mb: 2.5 }}
        >
          <Box sx={{ minWidth: 0, flexGrow: 1 }}>
            <Typography
              component="h1"
              sx={{ fontSize: { xs: 25, sm: 30 }, fontWeight: 850, lineHeight: 1.1 }}
            >
              Healthcare Movers
            </Typography>
            <Typography sx={{ color: 'text.secondary', mt: 0.5, fontSize: 13 }}>
              US healthcare equities ranked across completed trading sessions
            </Typography>
          </Box>
          <Tooltip title="Refresh ranking">
            <Box component="span" sx={{ flexShrink: 0 }}>
              <IconButton
                aria-label="Refresh healthcare movers"
                onClick={() => void refresh()}
                disabled={loading}
              >
                <RefreshIcon />
              </IconButton>
            </Box>
          </Tooltip>
        </Stack>

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {data?.status === 'unavailable' && (
          <Alert severity="info" sx={{ mb: 2 }}>
            {data.message ?? 'Healthcare mover data is not available yet.'}
          </Alert>
        )}

        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: {
              xs: 'repeat(2, minmax(0, 1fr))',
              sm: 'repeat(5, minmax(0, 1fr))',
            },
            gap: { xs: 2, sm: 3 },
            py: 1.75,
            px: { xs: 1, sm: 1.5 },
            mb: 2.5,
            borderTop: '1px solid',
            borderBottom: '1px solid',
            borderColor: 'divider',
          }}
        >
          <Stat label="Data through" value={formatDate(data?.as_of_date ?? null)} />
          <Stat label="Universe" value={`${data?.universe_count ?? 0} stocks`} />
          <Stat
            label="Coverage"
            value={`${((data?.coverage_fraction ?? 0) * 100).toFixed(1)}%`}
          />
          <Stat
            label={`Largest ${windowDays}D`}
            value={largestMove === null ? '-' : formatMove(largestMove)}
            accent={accent}
          />
          <Box sx={{ display: { xs: 'none', sm: 'block' } }}>
            <Stat label="Updated" value={formatTimestamp(data?.generated_at ?? null)} />
          </Box>
        </Box>

        <Box
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            alignItems: { xs: 'stretch', sm: 'center' },
            justifyContent: 'space-between',
            gap: 1.5,
            mb: 2,
          }}
        >
          <Tabs
            value={windowDays}
            onChange={(_event, value: number) => setWindowDays(value)}
            variant="fullWidth"
            sx={{
              minHeight: 58,
              maxWidth: { sm: 470 },
              flexGrow: { sm: 1 },
              '& .MuiTab-root': { minHeight: 58, minWidth: 54, px: 0.5, py: 0.5 },
            }}
          >
            {WINDOWS.map((window) => (
              <Tab
                key={window}
                value={window}
                aria-label={`${window} day movers refreshed ${refreshStamp.date} ${refreshStamp.time}`}
                label={
                  <Box sx={{ lineHeight: 1.1 }}>
                    <Typography component="span" sx={{ display: 'block', fontSize: 13, fontWeight: 800 }}>
                      {window}D
                    </Typography>
                    <Typography
                      component="span"
                      sx={{ display: 'block', color: 'text.secondary', fontSize: 9, mt: 0.3 }}
                    >
                      {refreshStamp.date}
                    </Typography>
                    <Typography
                      component="span"
                      sx={{ display: 'block', color: 'text.disabled', fontSize: 8.5 }}
                    >
                      {refreshStamp.time}
                    </Typography>
                  </Box>
                }
              />
            ))}
          </Tabs>
          <ToggleButtonGroup
            value={direction}
            exclusive
            size="small"
            onChange={(_event, value: 'gainers' | 'decliners' | null) => {
              if (value) setDirection(value)
            }}
            sx={{ alignSelf: { xs: 'flex-end', sm: 'center' } }}
          >
            <ToggleButton value="gainers" sx={{ gap: 0.75 }}>
              <TrendingUpIcon fontSize="small" />
              Gainers
            </ToggleButton>
            <ToggleButton value="decliners" sx={{ gap: 0.75 }}>
              <TrendingDownIcon fontSize="small" />
              Decliners
            </ToggleButton>
          </ToggleButtonGroup>
        </Box>

        <Box
          sx={{
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            mb: 1,
          }}
        >
          <Typography sx={{ fontWeight: 750, fontSize: 14 }}>
            Top {rows.length} {direction}
          </Typography>
          <Typography sx={{ color: 'text.secondary', fontSize: 11 }}>
            Median {medianMove === null ? '-' : formatMove(medianMove)}
          </Typography>
        </Box>

        <DesktopTable rows={rows} direction={direction} />
        <MobileList rows={rows} direction={direction} />

        {!loading && data?.status === 'current' && rows.length === 0 && (
          <Alert severity="info">No qualifying {direction} in this window.</Alert>
        )}

        <Typography sx={{ color: 'text.disabled', fontSize: 10.5, mt: 1.5 }}>
          FMP adjusted EOD closes · {windowDays} trading-session return · all active US healthcare
          equities covered · leaderboard requires $100K latest-session dollar volume
        </Typography>
      </Container>
    </Box>
  )
}

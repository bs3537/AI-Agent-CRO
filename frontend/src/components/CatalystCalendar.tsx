import { useMemo, useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import Divider from '@mui/material/Divider'
import IconButton from '@mui/material/IconButton'
import Link from '@mui/material/Link'
import Stack from '@mui/material/Stack'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import Typography from '@mui/material/Typography'
import CloseIcon from '@mui/icons-material/Close'
import EventIcon from '@mui/icons-material/Event'
import type { CatalystOutlookItem, PositionSummary } from '../types'

type Row = { ticker: string; item: CatalystOutlookItem; days: number | null }

// Portfolio-wide catalyst list: every holding's dated catalysts inside the
// chosen window, soonest first, with undated (estimated) items listed last.
export default function CatalystCalendar({
  positions,
  onOpenDetail,
}: {
  positions: PositionSummary[]
  onOpenDetail: (ticker: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [windowDays, setWindowDays] = useState(90)

  const { dated, undated } = useMemo(() => {
    const rows: Row[] = positions.flatMap((p) =>
      p.catalyst_outlook.map((item) => {
        const ts = item.date ? Date.parse(item.date) : Number.NaN
        return {
          ticker: p.ticker,
          item,
          days: Number.isNaN(ts) ? null : (ts - Date.now()) / 86_400_000,
        }
      }),
    )
    return {
      dated: rows
        .filter((r) => r.days !== null && r.days >= -1 && r.days <= windowDays)
        .sort((a, b) => (a.days ?? 0) - (b.days ?? 0)),
      undated: rows.filter((r) => r.days === null),
    }
  }, [positions, windowDays])

  const renderRow = (row: Row, index: number) => (
    <Box key={`${row.ticker}-${index}`} sx={{ display: 'flex', gap: 1.5, py: 1 }}>
      <Typography variant="body2" sx={{ width: 92, flexShrink: 0, fontWeight: 600 }}>
        {row.item.date ?? row.item.date_label}
      </Typography>
      <Link
        component="button"
        variant="body2"
        underline="hover"
        onClick={() => {
          setOpen(false)
          onOpenDetail(row.ticker)
        }}
        sx={{ width: 56, flexShrink: 0, fontWeight: 700, textAlign: 'left' }}
      >
        {row.ticker}
      </Link>
      <Typography variant="body2" sx={{ minWidth: 0 }}>
        {row.item.confirmed ? '' : '~'}
        {row.item.label}
      </Typography>
    </Box>
  )

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        startIcon={<EventIcon fontSize="small" />}
        onClick={() => setOpen(true)}
        sx={{ whiteSpace: 'nowrap' }}
      >
        Calendar
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="md">
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 2, pr: 1 }}>
          <Box sx={{ flexGrow: 1 }}>Catalyst calendar</Box>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={windowDays}
            onChange={(_, v: number | null) => v && setWindowDays(v)}
          >
            <ToggleButton value={30}>30d</ToggleButton>
            <ToggleButton value={60}>60d</ToggleButton>
            <ToggleButton value={90}>90d</ToggleButton>
          </ToggleButtonGroup>
          <IconButton size="small" onClick={() => setOpen(false)} aria-label="Close">
            <CloseIcon fontSize="small" />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers>
          {dated.length === 0 ? (
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              No dated catalysts in the next {windowDays} days.
            </Typography>
          ) : (
            <Stack divider={<Divider />}>{dated.map(renderRow)}</Stack>
          )}
          {undated.length > 0 && (
            <>
              <Typography
                variant="caption"
                sx={{ display: 'block', mt: 3, mb: 1, color: 'text.secondary', fontWeight: 700 }}
              >
                TIMING NOT YET ANNOUNCED ({undated.length})
              </Typography>
              <Stack divider={<Divider />}>{undated.map(renderRow)}</Stack>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

import { useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Collapse from '@mui/material/Collapse'
import Dialog from '@mui/material/Dialog'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import Divider from '@mui/material/Divider'
import IconButton from '@mui/material/IconButton'
import Link from '@mui/material/Link'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import CloseIcon from '@mui/icons-material/Close'
import EventIcon from '@mui/icons-material/Event'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import type { CatalystOutlookItem } from '../types'

const AMBER = '#F5B14C'

// Short colored tag per catalyst type.
const TAG: Record<CatalystOutlookItem['type'], { text: string; color: string }> = {
  regulatory: { text: 'REG', color: '#FF6A00' },
  clinical_data: { text: 'DATA', color: '#2F80FF' },
  product_launch: { text: 'LAUNCH', color: '#39D98A' },
  earnings: { text: 'EARNINGS', color: '#9AA0A6' },
  investor_event: { text: 'EVENT', color: '#9AA0A6' },
  contract_milestone: { text: 'MILESTONE', color: AMBER },
  fund_event: { text: 'FUND', color: '#A3D977' },
  other: { text: 'OTHER', color: '#9AA0A6' },
}

// Days from now until an ISO date, or null when there is no exact date.
function daysUntil(date: string | null): number | null {
  if (!date) return null
  const ts = Date.parse(date)
  return Number.isNaN(ts) ? null : (ts - Date.now()) / 86_400_000
}

// One expandable catalyst row: date + type tag + title, details on click.
function CatalystRow({ item }: { item: CatalystOutlookItem }) {
  const [open, setOpen] = useState(false)
  const tag = TAG[item.type] ?? TAG.other
  const days = daysUntil(item.date)
  const manual = item.source_url === 'manual' || !item.source_url
  return (
    <Box>
      <Box
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen((v) => !v)
          }
        }}
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          py: 1,
          cursor: 'pointer',
          '&:hover': { bgcolor: 'action.hover' },
        }}
      >
        <Typography
          variant="caption"
          sx={{ width: 74, flexShrink: 0, fontWeight: 700, color: tag.color }}
        >
          {tag.text}
        </Typography>
        <Box sx={{ minWidth: 0, flexGrow: 1 }}>
          <Typography
            variant="body2"
            sx={{
              fontWeight: 600,
              color: days !== null && days >= 0 && days <= 30 ? AMBER : 'text.primary',
            }}
          >
            {item.confirmed ? '' : '~'}
            {item.date ?? item.date_label}
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {item.label}
          </Typography>
        </Box>
        {open ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
      </Box>
      <Collapse in={open}>
        <Stack spacing={0.5} sx={{ pl: 10.5, pb: 1.25 }}>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Timing: {item.date_label}
            {item.confirmed ? ' (confirmed)' : ' (estimated)'}
          </Typography>
          {manual ? (
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Source: PM manual entry
            </Typography>
          ) : (
            <Link
              href={item.source_url}
              target="_blank"
              rel="noreferrer"
              variant="caption"
              underline="hover"
            >
              {item.source_title || item.source_url}
            </Link>
          )}
        </Stack>
      </Collapse>
    </Box>
  )
}

// Button for the catalysts column; opens a popup of the next-12-month catalysts.
export default function CatalystPopup({
  ticker,
  items,
}: {
  ticker: string
  items: CatalystOutlookItem[]
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        startIcon={<EventIcon fontSize="small" />}
        onClick={(event) => {
          event.stopPropagation()
          setOpen(true)
        }}
        aria-label={`Open ${ticker} catalysts`}
        sx={{ whiteSpace: 'nowrap', py: 0.1, fontSize: 11 }}
      >
        Catalysts ({items.length})
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', pr: 1 }}>
          <Box sx={{ flexGrow: 1 }}>{ticker} catalysts — next 12 months</Box>
          <IconButton size="small" onClick={() => setOpen(false)} aria-label="Close">
            <CloseIcon fontSize="small" />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers>
          {items.length === 0 ? (
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              No catalysts found yet. The weekly Sunday 6 PM ET search fills this in, or add
              one from the thesis drawer.
            </Typography>
          ) : (
            <Stack divider={<Divider />}>
              {items.map((item, index) => (
                <CatalystRow key={`${item.source_url}-${index}`} item={item} />
              ))}
            </Stack>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

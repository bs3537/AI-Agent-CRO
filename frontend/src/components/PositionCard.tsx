import { useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Chip from '@mui/material/Chip'
import Collapse from '@mui/material/Collapse'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import RefreshIcon from '@mui/icons-material/Refresh'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditNoteIcon from '@mui/icons-material/EditNote'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import type { PositionSummary } from '../types'
import { GRADE_HEX, VERDICT_HEX } from '../theme'
import DecisionChip from './DecisionChip'
import PnL from './PnL'
import Sparkline from './Sparkline'
import FileUpload from './FileUpload'

// One position tile: header (ticker + decision chip), economics row, the
// decision note + drivers, the inline thesis editor, and the action row
// (upload, recompute, details). All mutations call back up to App.
export default function PositionCard({
  pos,
  stale = false,
  onUpload,
  onRecompute,
  onDelete,
  onOpenThesis,
  onOpenDetail,
}: {
  pos: PositionSummary
  stale?: boolean
  onUpload: (ticker: string, file: File) => Promise<void>
  onRecompute: (ticker: string) => Promise<void>
  onDelete: (ticker: string) => Promise<void>
  onOpenThesis: (ticker: string) => void
  onOpenDetail: (ticker: string) => void
}) {
  const [recomputing, setRecomputing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const signal = pos.rating ?? pos.decision
  const note = signal?.note
  const drivers = signal?.drivers ?? []
  const confidence = signal?.confidence
  const modelUsed = signal?.model_used
  const decidedAt = signal?.decided_at
  const computeSource = signal?.compute_source
  const accent = pos.rating
    ? GRADE_HEX[pos.rating.grade] ?? '#888'
    : pos.decision ? VERDICT_HEX[pos.decision.color] ?? '#888' : '#444'
  // A thesis is a placeholder until the manager replaces the scaffolded stub.
  const thesisLabel = pos.thesis.trim().toUpperCase()
  const isStub = thesisLabel.startsWith('STUB') || thesisLabel.startsWith('PLACEHOLDER')

  // Recompute this position's decision, showing a busy label meanwhile.
  const recompute = async () => {
    setRecomputing(true)
    try {
      await onRecompute(pos.ticker)
    } finally {
      setRecomputing(false)
    }
  }

  const deleteHolding = async () => {
    setDeleting(true)
    try {
      await onDelete(pos.ticker)
      setConfirmDeleteOpen(false)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Card sx={{ borderLeft: `4px solid ${accent}` }}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Stack direction="row" spacing={1.5} alignItems="center">
            {/* Leading 1-year daily-close sparkline for the position. */}
            <Sparkline spark={pos.spark} />
            <Box>
              <Typography variant="h6">{pos.ticker}</Typography>
              <Typography variant="caption" sx={{ opacity: 0.7 }}>
                {pos.company_name ?? '—'} · tier {pos.conviction_tier} · {pos.stage}
              </Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={1} alignItems="center">
            <DecisionChip rating={pos.rating} decision={pos.decision} />
          </Stack>
        </Stack>

        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
          <PnL openPnl={pos.open_pnl} pnlPct={pos.pnl_pct} />
          <Chip size="small" variant="outlined" label={`${(pos.pct_nav * 100).toFixed(1)}% NAV`} />
          {/* Per-tile operational flags: stale pull (book-wide) + un-filled thesis. */}
          {stale && <Chip size="small" color="warning" label="STALE DATA" />}
          {isStub && <Chip size="small" color="warning" variant="outlined" label="STUB THESIS" />}
          {pos.rating && <TechnicalChip pos={pos} />}
          {pos.nearest_catalyst_days !== null && (
            <Chip
              size="small"
              variant="outlined"
              color={pos.has_overdue_catalyst ? 'error' : 'default'}
              label={`catalyst ${pos.nearest_catalyst_days}d`}
            />
          )}
          <Box sx={{ flexGrow: 1 }} />
          <Tooltip title={expanded ? 'Collapse holding details' : 'Expand holding details'}>
            <IconButton
              size="small"
              onClick={() => setExpanded((v) => !v)}
              sx={{ ml: 'auto', flexShrink: 0 }}
            >
              {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
        </Stack>

        <Collapse in={expanded} timeout="auto" unmountOnExit>
          {signal && (
            <Box sx={{ mt: 1.5 }}>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-line' }}>
                {note}
              </Typography>
              {drivers.length > 0 && (
                <Box sx={{ mt: 1 }}>
                  {drivers.map((d, i) => (
                    <Chip key={i} size="small" variant="outlined" label={d} sx={{ mr: 0.5, mb: 0.5 }} />
                  ))}
                </Box>
              )}
              <Typography variant="caption" sx={{ opacity: 0.5 }}>
                {modelUsed} · {sourceLabel(computeSource)} · conf{' '}
                {confidence !== undefined ? (confidence * 100).toFixed(0) : '—'}% ·{' '}
                {decidedAt ? new Date(decidedAt).toLocaleString() : '—'}
              </Typography>
            </Box>
          )}

          <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} alignItems="center" flexWrap="wrap">
            <Button
              size="small"
              variant="outlined"
              startIcon={<EditNoteIcon />}
              onClick={() => onOpenThesis(pos.ticker)}
            >
              Thesis
            </Button>
            <FileUpload onUpload={(file) => onUpload(pos.ticker, file)} />
            <Button
              size="small"
              variant="contained"
              startIcon={<RefreshIcon />}
              disabled={recomputing}
              onClick={() => void recompute()}
            >
              {recomputing ? 'Recomputing…' : 'Recompute'}
            </Button>
            <Button
              size="small"
              startIcon={<InfoOutlinedIcon />}
              onClick={() => onOpenDetail(pos.ticker)}
            >
              Details{pos.n_files > 0 ? ` (${pos.n_files} files)` : ''}
            </Button>
            <Button
              size="small"
              color="error"
              variant="outlined"
              startIcon={<DeleteOutlineIcon />}
              disabled={deleting || recomputing}
              onClick={() => setConfirmDeleteOpen(true)}
            >
              Delete
            </Button>
          </Stack>
        </Collapse>
      </CardContent>

      <Dialog open={confirmDeleteOpen} onClose={() => !deleting && setConfirmDeleteOpen(false)}>
        <DialogTitle>Delete {pos.ticker}?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            This removes the holding tile and ticker-owned monitor data from the local database,
            including decisions, ratings, scores, red-team passes, FMP/price snapshots, polls,
            uploads, and orphaned articles.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDeleteOpen(false)} disabled={deleting}>
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={() => void deleteHolding()}
            disabled={deleting}
          >
            {deleting ? 'Deleting…' : 'Delete holding'}
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  )
}

function sourceLabel(source: string | undefined) {
  const labels: Record<string, string> = {
    scheduler: 'auto scheduler',
    manual_single: 'manual tile',
    manual_all: 'manual all',
    unknown: 'legacy run',
  }
  return labels[source ?? 'unknown'] ?? source
}

function TechnicalChip({ pos }: { pos: PositionSummary }) {
  const state = pos.rating?.technical_state ?? 'no_price_data'
  const pct = pos.rating?.price_vs_ema20_pct
  const labels: Record<string, string> = {
    above_ema20: 'above 20-EMA',
    below_ema20: 'below 20-EMA',
    extended_below_ema20: 'extended below 20-EMA',
    no_price_data: 'no price data',
  }
  const label = pct !== null && pct !== undefined && state !== 'no_price_data'
    ? `${labels[state]} ${(pct * 100).toFixed(1)}%`
    : labels[state]
  const color: 'success' | 'default' | 'warning' =
    state === 'above_ema20' ? 'success' : state === 'no_price_data' ? 'default' : 'warning'
  return <Chip size="small" color={color} variant="outlined" label={label} />
}

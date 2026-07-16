import { useEffect, useState } from 'react'
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
import type { PositionSummary, QuoteInfo } from '../types'
import { GRADE_HEX, VERDICT_HEX } from '../theme'
import DecisionChip from './DecisionChip'
import PnL from './PnL'
import PriceTargetMetric from './PriceTargetMetric'
import Sparkline from './Sparkline'
import FileUpload from './FileUpload'
import ThesisTargetLine from './ThesisTargetLine'

// One position tile: header (ticker + decision chip), economics row, the
// decision note + drivers, the inline thesis editor, and the action row
// (upload, recompute, details). All mutations call back up to App.
export default function PositionCard({
  pos,
  liveQuote = null,
  stale = false,
  onUpload,
  onRecompute,
  onDelete,
  onOpenThesis,
  onOpenDetail,
}: {
  pos: PositionSummary
  liveQuote?: QuoteInfo | null
  stale?: boolean
  onUpload: (ticker: string, file: File) => Promise<void>
  onRecompute: (ticker: string) => Promise<void>
  onDelete: (ticker: string) => Promise<void>
  onOpenThesis: (ticker: string) => void
  onOpenDetail: (ticker: string) => void
}) {
  const [recomputing, setRecomputing] = useState(false)
  const [recomputingSecs, setRecomputingSecs] = useState(0)
  const [deleting, setDeleting] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)

  // Compute intraday P/L from live quote when available.
  // Display-only overlay — stored EOD values are never mutated.
  const livePrice = liveQuote?.price ?? null
  const intradayMv = livePrice != null && pos.qty != null ? livePrice * pos.qty : null
  const intradayPnl = intradayMv != null && pos.cost_basis != null ? intradayMv - pos.cost_basis : null
  const intradayPnlPct = intradayPnl != null && pos.cost_basis ? intradayPnl / pos.cost_basis : null
  const isLive = intradayMv !== null
  const displayPnl = intradayPnl !== null ? intradayPnl : pos.open_pnl
  const displayPnlPct = intradayPnlPct !== null ? intradayPnlPct : pos.pnl_pct
  // Daily % change for the stock sourced from Yahoo Finance (primary) or FMP (fallback).
  const dayChangePct = liveQuote?.change_pct ?? null
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
  const isStub = pos.thesis_source === 'system_stub' || thesisLabel.startsWith('STUB') || thesisLabel.startsWith('PLACEHOLDER')
  const isAiDraft = pos.is_ai_generated_thesis || (pos.thesis_source === 'ai_generated' && pos.thesis_status === 'draft')
  const thesisPreview = previewText(pos.thesis)
  const preliminary = isAiDraft ? pos.preliminary_thesis : null

  useEffect(() => {
    if (!recomputing) { setRecomputingSecs(0); return }
    setRecomputingSecs(0)
    const id = setInterval(() => setRecomputingSecs((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [recomputing])

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
          <PnL openPnl={displayPnl} pnlPct={displayPnlPct} live={isLive} />
          <Chip size="small" variant="outlined" label={`${(pos.pct_nav * 100).toFixed(1)}% NAV`} />
          {dayChangePct != null && (
            <Tooltip title="Today's price change % (Yahoo Finance primary · FMP fallback · 30-min refresh)">
              <Chip
                size="small"
                variant="outlined"
                color={dayChangePct >= 0 ? 'success' : 'error'}
                label={`${dayChangePct >= 0 ? '+' : ''}${dayChangePct.toFixed(2)}% today`}
              />
            </Tooltip>
          )}
          {!pos.is_etf && <PriceTargetMetric target={pos.analyst_target} />}
          {/* Per-tile operational flags: stale pull (book-wide) + un-filled thesis. */}
          {stale && <Chip size="small" color="warning" label="STALE DATA" />}
          {isAiDraft && (
            <Chip
              size="small"
              color="info"
              variant="outlined"
              label={`PRELIMINARY THESIS${pos.draft_rating_grade ? ` · ${pos.draft_rating_grade}` : ''}`}
            />
          )}
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
              {isAiDraft && (
                <Typography variant="caption" display="block" sx={{ opacity: 0.65, mt: 0.5 }}>
                  AI-generated preliminary thesis · {pos.thesis_generated_by ?? 'model'}
                  {pos.thesis_generated_at ? ` · ${new Date(pos.thesis_generated_at).toLocaleString()}` : ''}.
                </Typography>
              )}
            </Box>
          )}

          <Box
            sx={{
              mt: 1.5,
              pt: 1.5,
              borderTop: '1px solid',
              borderColor: 'divider',
            }}
          >
            <Typography variant="subtitle2" sx={{ color: 'primary.main', mb: 0.5 }}>
              {isAiDraft ? 'Preliminary thesis' : 'Current thesis'}
            </Typography>
            {preliminary ? (
              <PreliminaryThesisPreview pos={pos} />
            ) : (
              <Typography variant="body2" sx={{ whiteSpace: 'pre-line' }}>
                {thesisPreview || 'No thesis saved.'}
              </Typography>
            )}
            {!preliminary && pos.thesis.length > thesisPreview.length && (
              <Typography variant="caption" display="block" sx={{ opacity: 0.55, mt: 0.5 }}>
                Preview shown · open Thesis for the full text.
              </Typography>
            )}
            <Box sx={{ mt: 1 }}>
              <ThesisTargetLine target={pos.analyst_target} isEtf={pos.is_etf} />
            </Box>
            {preliminary && (
              <Typography variant="caption" display="block" sx={{ opacity: 0.55, mt: 0.75 }}>
                Researched {new Date(preliminary.researched_at).toLocaleDateString()}
                {preliminary.research_sources.length > 0
                  ? ` · ${preliminary.research_sources.length} sources`
                  : ''}
                {' · '}open Thesis for the complete draft.
              </Typography>
            )}
          </Box>

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
              {recomputing ? `Recomputing… ${recomputingSecs}s` : 'Recompute'}
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
            including decisions, ratings, scores, red-team passes, analyst targets,
            FMP/price snapshots, polls, uploads, and orphaned articles.
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
    scheduler_morning_full_codex: 'morning full-book Codex',
    scheduler_new_position_draft: 'new-position AI draft',
    manual_new_position_draft: 'manual new-position AI draft',
    manual_preliminary_thesis: 'manual preliminary research',
    manual_preliminary_thesis_cli: 'CLI preliminary research',
    hermes_manual_preliminary_thesis: 'Hermes preliminary research',
    hermes_preliminary_thesis_one: 'Hermes preliminary research',
    hermes_preliminary_thesis_backfill: 'Hermes preliminary backfill',
    preliminary_thesis_followup: 'preliminary thesis follow-up',
    unknown: 'legacy run',
  }
  return labels[source ?? 'unknown'] ?? source
}

function previewText(text: string, maxChars = 520): string {
  const normalized = text.trim().replace(/\s+/g, ' ')
  if (normalized.length <= maxChars) return normalized
  return `${normalized.slice(0, maxChars).trimEnd()}…`
}

function PreliminaryThesisPreview({ pos }: { pos: PositionSummary }) {
  const thesis = pos.preliminary_thesis
  if (!thesis) return null
  return (
    <Stack spacing={1}>
      <Typography variant="body2">
        {previewText(thesis.investment_case, 480)}
      </Typography>
      <Box>
        <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 700 }}>
          Moat
        </Typography>
        <Typography variant="body2">{previewText(thesis.moat, 260)}</Typography>
      </Box>
      <Box>
        <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 700 }}>
          Catalysts
        </Typography>
        {thesis.catalysts.slice(0, 2).map((item) => (
          <Typography key={item} variant="body2" sx={{ pl: 1.25 }}>
            · {previewText(item, 220)}
          </Typography>
        ))}
      </Box>
      <Box>
        <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 700 }}>
          Differentiation
        </Typography>
        <Typography variant="body2">{previewText(thesis.differentiation, 260)}</Typography>
      </Box>
      {thesis.risks.length > 0 && (
        <Box>
          <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 700 }}>
            Key risk
          </Typography>
          <Typography variant="body2">{previewText(thesis.risks[0], 220)}</Typography>
        </Box>
      )}
    </Stack>
  )
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

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import IconButton from '@mui/material/IconButton'
import Link from '@mui/material/Link'
import List from '@mui/material/List'
import ListItem from '@mui/material/ListItem'
import ListItemText from '@mui/material/ListItemText'
import Stack from '@mui/material/Stack'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import CloseIcon from '@mui/icons-material/Close'
import DeleteIcon from '@mui/icons-material/Delete'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditNoteIcon from '@mui/icons-material/EditNote'
import RefreshIcon from '@mui/icons-material/Refresh'
import { api } from '../api'
import { compactUsd, sourceLabel } from '../format'
import type {
  PositionDetail,
  PositionSummary,
  QuoteInfo,
} from '../types'
import DecisionChip from './DecisionChip'
import FileUpload from './FileUpload'
import PnL from './PnL'
import PriceTargetMetric from './PriceTargetMetric'
import Sparkline from './Sparkline'
import { TechnicalChip } from './TechnicalChip'
import ThesisTargetLine from './ThesisTargetLine'

const DETAIL_WIDTH = 480
const THESIS_WIDTH = 520

export default function HoldingDrawer({
  ticker,
  position,
  liveQuote,
  stale,
  thesisOpen,
  onClose,
  onOpenThesis,
  onUpload,
  onRecompute,
  onDelete,
  onChanged,
}: {
  ticker: string | null
  position: PositionSummary | null
  liveQuote: QuoteInfo | null
  stale: boolean
  thesisOpen: boolean
  onClose: () => void
  onOpenThesis: (ticker: string) => void
  onUpload: (ticker: string, file: File) => Promise<void>
  onRecompute: (ticker: string) => Promise<void>
  onDelete: (ticker: string) => Promise<void>
  onChanged: () => void
}) {
  const [detail, setDetail] = useState<PositionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recomputing, setRecomputing] = useState(false)
  const [recomputingSeconds, setRecomputingSeconds] = useState(0)
  const [deleting, setDeleting] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)

  useEffect(() => {
    setDetail(null)
    setError(null)
    if (!ticker) return
    api.detail(ticker).then(setDetail).catch((reason) => setError(String(reason)))
  }, [ticker])

  useEffect(() => {
    if (!recomputing) {
      setRecomputingSeconds(0)
      return
    }
    const timer = window.setInterval(
      () => setRecomputingSeconds((seconds) => seconds + 1),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [recomputing])

  const signal = position?.rating ?? position?.decision
  const thesisLabel = (position?.thesis ?? '').trim().toUpperCase()
  const isStub =
    position?.thesis_source === 'system_stub' ||
    thesisLabel.startsWith('STUB') ||
    thesisLabel.startsWith('PLACEHOLDER')
  const isAiDraft = Boolean(
    position?.is_ai_generated_thesis ||
      (position?.thesis_source === 'ai_generated' &&
        position?.thesis_status === 'draft'),
  )

  const intradayValue =
    position && liveQuote ? liveQuote.price * position.qty : null
  const intradayPnl =
    intradayValue !== null && position?.cost_basis !== null
      ? intradayValue - (position?.cost_basis ?? 0)
      : null
  const intradayPnlPct =
    intradayPnl !== null && position?.cost_basis
      ? intradayPnl / position.cost_basis
      : null

  const recompute = async () => {
    if (!ticker) return
    setRecomputing(true)
    setError(null)
    try {
      await onRecompute(ticker)
      setDetail(await api.detail(ticker))
    } catch (reason) {
      setError(String(reason))
    } finally {
      setRecomputing(false)
    }
  }

  const upload = async (file: File) => {
    if (!ticker) return
    setError(null)
    try {
      await onUpload(ticker, file)
      setDetail(await api.detail(ticker))
    } catch (reason) {
      setError(String(reason))
    }
  }

  const removeFile = async (eventId: string) => {
    if (!ticker) return
    await api.deleteFile(ticker, eventId)
    setDetail(await api.detail(ticker))
    onChanged()
  }

  const deleteHolding = async () => {
    if (!ticker) return
    setDeleting(true)
    try {
      await onDelete(ticker)
      setConfirmDeleteOpen(false)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Drawer
      anchor="right"
      variant="persistent"
      open={Boolean(ticker)}
      PaperProps={{
        sx: (theme) => ({
          width: { xs: '100vw', sm: DETAIL_WIDTH },
          right: { xs: 0, md: thesisOpen ? `${THESIS_WIDTH}px` : 0 },
          transition: `${theme.transitions.create(['transform', 'right'], {
            duration: theme.transitions.duration.enteringScreen,
          })} !important`,
          zIndex: theme.zIndex.drawer,
          borderLeft: '1px solid',
          borderColor: 'divider',
        }),
      }}
    >
      <Box sx={{ p: { xs: 1.5, sm: 2 } }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.25}>
          <Sparkline spark={position?.spark ?? null} width={112} height={34} />
          <Box sx={{ minWidth: 0, flexGrow: 1 }}>
            <Typography variant="h6">{ticker}</Typography>
            <Typography
              variant="caption"
              noWrap
              sx={{ color: 'text.secondary', display: 'block' }}
            >
              {position?.company_name ?? '-'}
            </Typography>
          </Box>
          <DecisionChip
            rating={position?.rating}
            decision={position?.decision ?? null}
          />
          <Tooltip title="Close details">
            <IconButton onClick={onClose} size="small" aria-label="Close details">
              <CloseIcon />
            </IconButton>
          </Tooltip>
        </Stack>

        {position && (
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
            sx={{ mt: 1.25 }}
          >
            <PnL
              openPnl={intradayPnl ?? position.open_pnl}
              pnlPct={intradayPnlPct ?? position.pnl_pct}
              live={intradayPnl !== null}
            />
            <Chip
              size="small"
              variant="outlined"
              label={`${(position.pct_nav * 100).toFixed(1)}% NAV`}
            />
            {liveQuote && (
              <Chip
                size="small"
                color={liveQuote.change_pct >= 0 ? 'success' : 'error'}
                variant="outlined"
                label={`${liveQuote.change_pct >= 0 ? '+' : ''}${liveQuote.change_pct.toFixed(2)}% today`}
              />
            )}
            {!position.is_etf && (
              <PriceTargetMetric target={position.analyst_target} />
            )}
            {stale && <Chip size="small" color="warning" label="STALE DATA" />}
            {isAiDraft && (
              <Chip
                size="small"
                color="info"
                variant="outlined"
                label="PRELIMINARY THESIS"
              />
            )}
            {isStub && (
              <Chip
                size="small"
                color="warning"
                variant="outlined"
                label="STUB THESIS"
              />
            )}
            <TechnicalChip pos={position} />
          </Stack>
        )}

        {signal && (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-line' }}>
              {signal.note}
            </Typography>
            {signal.drivers.length > 0 && (
              <Box sx={{ mt: 0.75 }}>
                {signal.drivers.map((driver) => (
                  <Chip
                    key={driver}
                    size="small"
                    variant="outlined"
                    label={driver}
                    sx={{ mr: 0.5, mb: 0.5 }}
                  />
                ))}
              </Box>
            )}
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {signal.model_used} | {sourceLabel(signal.compute_source)} | conf{' '}
              {(signal.confidence * 100).toFixed(0)}% |{' '}
              {new Date(signal.decided_at).toLocaleString()}
            </Typography>
          </Box>
        )}

        {ticker && (
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
            sx={{ mt: 1.5 }}
          >
            <Button
              size="small"
              variant="outlined"
              startIcon={<EditNoteIcon />}
              onClick={() => onOpenThesis(ticker)}
            >
              Thesis
            </Button>
            <FileUpload onUpload={upload} />
            <Button
              size="small"
              variant="contained"
              startIcon={<RefreshIcon />}
              disabled={recomputing}
              onClick={() => void recompute()}
            >
              {recomputing
                ? `Recomputing ${recomputingSeconds}s`
                : 'Recompute'}
            </Button>
            <Tooltip title="Delete holding and ticker-owned monitor data">
              <IconButton
                size="small"
                color="error"
                disabled={deleting || recomputing}
                onClick={() => setConfirmDeleteOpen(true)}
                aria-label={`Delete ${ticker}`}
              >
                <DeleteOutlineIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        )}

        <Divider sx={{ my: 1.5 }} />

        {error && <Typography color="error">{error}</Typography>}
        {!detail && !error && (
          <Typography sx={{ color: 'text.secondary' }}>Loading...</Typography>
        )}

        {detail && (
          <Stack spacing={2}>
            <Section title="Thesis">
              <Typography variant="body2" sx={{ whiteSpace: 'pre-line' }}>
                {detail.thesis || 'No thesis saved.'}
              </Typography>
              <Box sx={{ mt: 0.75 }}>
                <ThesisTargetLine
                  target={detail.analyst_target}
                  isEtf={detail.is_etf}
                />
              </Box>
            </Section>

            <Section title={`Upcoming research catalysts (${detail.catalyst_outlook.length})`}>
              <List dense disablePadding>
                {detail.catalyst_outlook.map((item, index) => (
                  <ListItem
                    key={`${item.source_url}-${index}`}
                    disableGutters
                    alignItems="flex-start"
                  >
                    <ListItemText
                      primary={
                        <Stack direction="row" spacing={0.75} alignItems="center">
                          <Chip
                            size="small"
                            variant="outlined"
                            label={item.type.split('_').join(' ')}
                          />
                          <Link
                            href={item.source_url}
                            target="_blank"
                            rel="noreferrer"
                            variant="body2"
                          >
                            {item.label}
                          </Link>
                        </Stack>
                      }
                      secondary={`${item.date_label}${item.confirmed ? ' | confirmed' : ' | estimated'} | ${item.source_title}`}
                    />
                  </ListItem>
                ))}
                {detail.catalyst_outlook.length === 0 && (
                  <EmptyRow text="No sourced upcoming catalysts cached yet." />
                )}
              </List>
            </Section>

            <Section title="Rating detail">
              {detail.rating ? (
                <List dense disablePadding>
                  <ListItem disableGutters>
                    <ListItemText
                      primary={`${detail.rating.action.toUpperCase()} ${detail.rating.grade} | ${detail.rating.attention_state}`}
                      secondary={`Risk score ${detail.rating.risk_score.toFixed(1)} | ${sourceLabel(detail.rating.compute_source)} | ${detail.rating.model_used}`}
                    />
                  </ListItem>
                  {Object.entries(detail.rating.risk_components).map(
                    ([key, value]) => (
                      <MetricRow
                        key={key}
                        label={RISK_LABELS[key] ?? key}
                        value={Number(value).toFixed(1)}
                      />
                    ),
                  )}
                  <MetricRow
                    label="20-day EMA"
                    value={formatTechnical(
                      detail.rating.price_vs_ema20_pct,
                      detail.rating.technical_state,
                    )}
                  />
                </List>
              ) : (
                <EmptyRow text="No rating yet." />
              )}
            </Section>

            <Section title={`Scored articles (${detail.scores.length})`}>
              <List dense disablePadding>
                {detail.scores.map((score) => (
                  <ListItem
                    key={score.score_event_id}
                    disableGutters
                    alignItems="flex-start"
                  >
                    <ListItemText
                      primary={
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Chip
                            size="small"
                            label={`#${score.primary_bucket_id} | ${score.composite.toFixed(1)}`}
                          />
                          {score.url ? (
                            <Link
                              href={score.url}
                              target="_blank"
                              rel="noreferrer"
                              variant="body2"
                            >
                              {score.title}
                            </Link>
                          ) : (
                            <Typography variant="body2">{score.title}</Typography>
                          )}
                        </Stack>
                      }
                      secondary={score.rationale}
                    />
                  </ListItem>
                ))}
                {detail.scores.length === 0 && (
                  <EmptyRow text="No scored articles." />
                )}
              </List>
            </Section>

            <Section title={`Red-team bear cases (${detail.red_team.length})`}>
              <List dense disablePadding>
                {detail.red_team.map((bearCase) => (
                  <ListItem
                    key={bearCase.pass_event_id}
                    disableGutters
                    alignItems="flex-start"
                  >
                    <ListItemText
                      primary={
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Chip
                            size="small"
                            color="warning"
                            label={`severity ${bearCase.severity_of_concern}/5`}
                          />
                          <Typography variant="body2">
                            {bearCase.title}
                          </Typography>
                        </Stack>
                      }
                      secondary={
                        <>
                          {bearCase.bearish_thesis}
                          {bearCase.matched_patterns.length > 0 && (
                            <Box sx={{ mt: 0.5 }}>
                              {bearCase.matched_patterns.map((pattern) => (
                                <Chip
                                  key={pattern}
                                  size="small"
                                  variant="outlined"
                                  label={pattern}
                                  sx={{ mr: 0.5, mb: 0.5 }}
                                />
                              ))}
                            </Box>
                          )}
                        </>
                      }
                    />
                  </ListItem>
                ))}
                {detail.red_team.length === 0 && (
                  <EmptyRow text="No red-team passes." />
                )}
              </List>
            </Section>

            <Section title={`Files (${detail.files.length})`}>
              <List dense disablePadding>
                {detail.files.map((file) => (
                  <ListItem
                    key={file.event_id}
                    disableGutters
                    secondaryAction={
                      <Tooltip title={`Delete ${file.filename}`}>
                        <IconButton
                          edge="end"
                          size="small"
                          aria-label={`Delete ${file.filename}`}
                          onClick={() => void removeFile(file.event_id)}
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    }
                  >
                    <ListItemText
                      primary={file.filename}
                      secondary={`${file.content_type} | ${file.n_chars} chars`}
                    />
                  </ListItem>
                ))}
                {detail.files.length === 0 && (
                  <EmptyRow text="No uploaded documents." />
                )}
              </List>
            </Section>

            <Section title="Financials (FMP)">
              {detail.financials && Object.keys(detail.financials).length > 0 ? (
                <List dense disablePadding>
                  {orderedFinancials(detail.financials).map(([key, value]) => (
                    <MetricRow
                      key={key}
                      label={FINANCIAL_LABELS[key] ?? key}
                      value={formatFinancial(key, value)}
                    />
                  ))}
                </List>
              ) : (
                <EmptyRow text="No FMP financial snapshot yet." />
              )}
            </Section>

            <Section title={`PM catalysts (${detail.catalysts.length})`}>
              <List dense disablePadding>
                {detail.catalysts.map((catalyst, index) => (
                  <ListItem key={`${catalyst.date}-${index}`} disableGutters>
                    <ListItemText
                      primary={`${catalyst.date} | ${catalyst.type}`}
                      secondary={catalyst.description}
                    />
                  </ListItem>
                ))}
                {detail.catalysts.length === 0 && (
                  <EmptyRow text="No PM catalyst dates on file." />
                )}
              </List>
            </Section>
          </Stack>
        )}
      </Box>

      <Dialog
        open={confirmDeleteOpen}
        onClose={() => !deleting && setConfirmDeleteOpen(false)}
      >
        <DialogTitle>Delete {ticker}?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            This removes the holding and its ticker-owned ratings, evidence,
            analyst targets, catalyst outlook, financial snapshots, uploads,
            and orphaned articles.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setConfirmDeleteOpen(false)}
            disabled={deleting}
          >
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={() => void deleteHolding()}
            disabled={deleting}
          >
            {deleting ? 'Deleting...' : 'Delete holding'}
          </Button>
        </DialogActions>
      </Dialog>
    </Drawer>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Box>
      <Typography variant="subtitle2" sx={{ color: 'primary.main', mb: 0.5 }}>
        {title}
      </Typography>
      {children}
    </Box>
  )
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <ListItem disableGutters sx={{ py: 0.1 }}>
      <ListItemText
        primary={
          <Stack direction="row" justifyContent="space-between" spacing={2}>
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              {label}
            </Typography>
            <Typography
              variant="body2"
              sx={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
            >
              {value}
            </Typography>
          </Stack>
        }
      />
    </ListItem>
  )
}

const FINANCIAL_LABELS: Record<string, string> = {
  company: 'Company',
  sector: 'Sector',
  market_cap: 'Market cap',
  enterprise_value: 'Enterprise value',
  price: 'Price',
  beta: 'Beta',
  pe_ttm: 'P/E (TTM)',
  current_ratio: 'Current ratio',
  quick_ratio: 'Quick ratio',
  debt_to_equity: 'Debt / equity',
  gross_margin: 'Gross margin',
  net_margin: 'Net margin',
  cash_per_share: 'Cash / share',
  fcf_per_share: 'FCF / share',
}

const FINANCIAL_ORDER = [
  'company',
  'sector',
  'market_cap',
  'enterprise_value',
  'price',
  'beta',
  'pe_ttm',
  'current_ratio',
  'quick_ratio',
  'debt_to_equity',
  'gross_margin',
  'net_margin',
  'cash_per_share',
  'fcf_per_share',
]

const RISK_LABELS: Record<string, string> = {
  red_team_severity: 'Red-team severity',
  thesis_clause_impact: 'Thesis clause impact',
  top_article_composite: 'Top article composite',
  catalyst_timing: 'Catalyst timing',
  technical_trend: 'Technical trend',
  unrealized_loss: 'Unrealized loss',
  data_quality: 'Data quality',
}

function orderedFinancials(
  financials: Record<string, unknown>,
): [string, unknown][] {
  const known = FINANCIAL_ORDER.filter(
    (key) => financials[key] !== undefined && financials[key] !== null,
  ).map((key) => [key, financials[key]] as [string, unknown])
  const extra = Object.entries(financials).filter(
    ([key]) => !FINANCIAL_ORDER.includes(key),
  )
  return [...known, ...extra]
}

function formatFinancial(key: string, value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number') {
    if (key.endsWith('margin')) return `${(value * 100).toFixed(1)}%`
    if (key === 'market_cap' || key === 'enterprise_value') {
      return compactUsd(value)
    }
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
  return String(value)
}

function formatTechnical(pct: number | null, state: string): string {
  const label = state.split('_').join(' ')
  if (pct === null || pct === undefined) return label
  return `${label} | ${(pct * 100).toFixed(1)}%`
}

function EmptyRow({ text }: { text: string }) {
  return (
    <Typography variant="body2" sx={{ color: 'text.secondary', py: 0.5 }}>
      {text}
    </Typography>
  )
}

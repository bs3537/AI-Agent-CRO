import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import IconButton from '@mui/material/IconButton'
import Link from '@mui/material/Link'
import List from '@mui/material/List'
import ListItem from '@mui/material/ListItem'
import ListItemText from '@mui/material/ListItemText'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import CloseIcon from '@mui/icons-material/Close'
import DeleteIcon from '@mui/icons-material/Delete'
import { api } from '../api'
import type { PositionDetail } from '../types'

// Right-hand drawer with the full evidence trail for one position: scored
// articles, red-team bear cases, uploaded files (deletable), and catalysts.
// Fetches detail when opened on a ticker.
export default function DetailDrawer({
  ticker,
  onClose,
  onChanged,
}: {
  ticker: string | null
  onClose: () => void
  onChanged: () => void
}) {
  const [detail, setDetail] = useState<PositionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Load (or clear) detail whenever the open ticker changes.
  useEffect(() => {
    setDetail(null)
    setError(null)
    if (!ticker) return
    api
      .detail(ticker)
      .then(setDetail)
      .catch((e) => setError(String(e)))
  }, [ticker])

  // Delete an uploaded file, then refresh the drawer + parent grid.
  const removeFile = async (eventId: string) => {
    if (!ticker) return
    await api.deleteFile(ticker, eventId)
    const fresh = await api.detail(ticker)
    setDetail(fresh)
    onChanged()
  }

  return (
    <Drawer anchor="right" open={!!ticker} onClose={onClose}>
      <Box sx={{ width: { xs: 340, sm: 460 }, p: 2 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Typography variant="h6">{ticker} — detail</Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Stack>
        <Divider sx={{ my: 1.5 }} />

        {error && <Typography color="error">{error}</Typography>}
        {!detail && !error && <Typography sx={{ opacity: 0.6 }}>Loading…</Typography>}

        {detail && (
          <Stack spacing={2}>
            <Section title="Rating">
              {detail.rating ? (
                <List dense disablePadding>
                  <ListItem disableGutters>
                    <ListItemText
                      primary={`${detail.rating.action.toUpperCase()} ${detail.rating.grade} · ${detail.rating.attention_state}`}
                      secondary={`Risk score ${detail.rating.risk_score.toFixed(1)} · ${sourceLabel(detail.rating.compute_source)} · ${detail.rating.model_used}`}
                    />
                  </ListItem>
                  {Object.entries(detail.rating.risk_components).map(([k, v]) => (
                    <ListItem key={k} disableGutters sx={{ py: 0.1 }}>
                      <ListItemText
                        primary={
                          <Stack direction="row" justifyContent="space-between" spacing={2}>
                            <Typography variant="body2" sx={{ opacity: 0.7 }}>
                              {RISK_LABELS[k] ?? k}
                            </Typography>
                            <Typography variant="body2">{Number(v).toFixed(1)}</Typography>
                          </Stack>
                        }
                      />
                    </ListItem>
                  ))}
                  <ListItem disableGutters sx={{ py: 0.1 }}>
                    <ListItemText
                      primary={
                        <Stack direction="row" justifyContent="space-between" spacing={2}>
                          <Typography variant="body2" sx={{ opacity: 0.7 }}>
                            20-day EMA
                          </Typography>
                          <Typography variant="body2">
                            {fmtTechnical(detail.rating.price_vs_ema20_pct, detail.rating.technical_state)}
                          </Typography>
                        </Stack>
                      }
                    />
                  </ListItem>
                </List>
              ) : (
                <EmptyRow text="No rating yet." />
              )}
            </Section>

            <Section title={`Scored articles (${detail.scores.length})`}>
              <List dense disablePadding>
                {detail.scores.map((s) => (
                  <ListItem key={s.score_event_id} disableGutters alignItems="flex-start">
                    <ListItemText
                      primary={
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Chip
                            size="small"
                            label={`#${s.primary_bucket_id} · ${s.composite.toFixed(1)}`}
                          />
                          {s.url ? (
                            <Link href={s.url} target="_blank" rel="noreferrer" variant="body2">
                              {s.title}
                            </Link>
                          ) : (
                            <Typography variant="body2">{s.title}</Typography>
                          )}
                        </Stack>
                      }
                      secondary={s.rationale}
                    />
                  </ListItem>
                ))}
                {detail.scores.length === 0 && <EmptyRow text="No scored articles." />}
              </List>
            </Section>

            <Section title={`Red-team bear cases (${detail.red_team.length})`}>
              <List dense disablePadding>
                {detail.red_team.map((b) => (
                  <ListItem key={b.pass_event_id} disableGutters alignItems="flex-start">
                    <ListItemText
                      primary={
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Chip size="small" color="warning" label={`sev ${b.severity_of_concern}/5`} />
                          <Typography variant="body2">{b.title}</Typography>
                        </Stack>
                      }
                      secondary={
                        <>
                          {b.bearish_thesis}
                          {b.matched_patterns.length > 0 && (
                            <Box sx={{ mt: 0.5 }}>
                              {b.matched_patterns.map((p) => (
                                <Chip key={p} size="small" variant="outlined" label={p} sx={{ mr: 0.5, mb: 0.5 }} />
                              ))}
                            </Box>
                          )}
                        </>
                      }
                    />
                  </ListItem>
                ))}
                {detail.red_team.length === 0 && <EmptyRow text="No red-team passes." />}
              </List>
            </Section>

            <Section title={`Files (${detail.files.length})`}>
              <List dense disablePadding>
                {detail.files.map((f) => (
                  <ListItem
                    key={f.event_id}
                    disableGutters
                    secondaryAction={
                      <IconButton edge="end" size="small" onClick={() => void removeFile(f.event_id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    }
                  >
                    <ListItemText primary={f.filename} secondary={`${f.content_type} · ${f.n_chars} chars`} />
                  </ListItem>
                ))}
                {detail.files.length === 0 && <EmptyRow text="No uploaded documents." />}
              </List>
            </Section>

            <Section title="Financials (FMP)">
              {detail.financials && Object.keys(detail.financials).length > 0 ? (
                <List dense disablePadding>
                  {orderedFinancials(detail.financials).map(([k, v]) => (
                    <ListItem key={k} disableGutters sx={{ py: 0.1 }}>
                      <ListItemText
                        primary={
                          <Stack direction="row" justifyContent="space-between" spacing={2}>
                            <Typography variant="body2" sx={{ opacity: 0.7 }}>
                              {FINANCIAL_LABELS[k] ?? k}
                            </Typography>
                            <Typography variant="body2">{fmtFinancial(k, v)}</Typography>
                          </Stack>
                        }
                      />
                    </ListItem>
                  ))}
                </List>
              ) : (
                <EmptyRow text="No financials yet (set FMP_API_KEY + run a collect)." />
              )}
            </Section>

            <Section title={`Catalysts (${detail.catalysts.length})`}>
              <List dense disablePadding>
                {detail.catalysts.map((c, i) => (
                  <ListItem key={i} disableGutters>
                    <ListItemText primary={`${c.date} · ${c.type}`} secondary={c.description} />
                  </ListItem>
                ))}
                {detail.catalysts.length === 0 && <EmptyRow text="No catalysts on file." />}
              </List>
            </Section>
          </Stack>
        )}
      </Box>
    </Drawer>
  )
}

// A titled section block.
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box>
      <Typography variant="subtitle2" sx={{ color: 'primary.main', mb: 0.5 }}>
        {title}
      </Typography>
      {children}
    </Box>
  )
}

// Friendly labels + display order for the FMP metric keys (keys not listed
// here still render, after the known ones, using the raw key).
const FINANCIAL_LABELS: Record<string, string> = {
  company: 'Company', sector: 'Sector', market_cap: 'Market cap', price: 'Price',
  beta: 'Beta', pe_ttm: 'P/E (TTM)', current_ratio: 'Current ratio',
  quick_ratio: 'Quick ratio', debt_to_equity: 'Debt / equity',
  gross_margin: 'Gross margin', net_margin: 'Net margin',
  cash_per_share: 'Cash / share', fcf_per_share: 'FCF / share',
  enterprise_value: 'Enterprise value',
}
const FINANCIAL_ORDER = [
  'company', 'sector', 'market_cap', 'enterprise_value', 'price', 'beta', 'pe_ttm',
  'current_ratio', 'quick_ratio', 'debt_to_equity', 'gross_margin', 'net_margin',
  'cash_per_share', 'fcf_per_share',
]

const RISK_LABELS: Record<string, string> = {
  red_team_severity: 'Red-team severity',
  thesis_clause_impact: 'Thesis clause impact',
  top_article_composite: 'Top article composite',
  catalyst_timing: 'Catalyst timing',
  technical_trend: 'Technical trend',
  data_quality: 'Data quality',
}

// Order the financials dict by FINANCIAL_ORDER, then append any extra keys.
function orderedFinancials(f: Record<string, unknown>): [string, unknown][] {
  const known = FINANCIAL_ORDER.filter((k) => f[k] !== undefined && f[k] !== null).map(
    (k) => [k, f[k]] as [string, unknown],
  )
  const extra = Object.entries(f).filter(([k]) => !FINANCIAL_ORDER.includes(k))
  return [...known, ...extra]
}

// Format one metric: margins as %, market cap / EV compacted to B/T, other
// numbers to ≤2 decimals, strings verbatim.
function fmtFinancial(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number') {
    if (key.endsWith('margin')) return `${(value * 100).toFixed(1)}%`
    if (key === 'market_cap' || key === 'enterprise_value') return compactUsd(value)
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
  return String(value)
}

// Compact a large dollar figure to T / B / M suffixes.
function compactUsd(n: number): string {
  const abs = Math.abs(n)
  if (abs >= 1e12) return `${(n / 1e12).toFixed(1)}T`
  if (abs >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

function fmtTechnical(pct: number | null, state: string): string {
  const label = state.split('_').join(' ')
  if (pct === null || pct === undefined) return label
  return `${label} · ${(pct * 100).toFixed(1)}%`
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

// Faint placeholder for an empty list.
function EmptyRow({ text }: { text: string }) {
  return (
    <Typography variant="body2" sx={{ opacity: 0.5, py: 0.5 }}>
      {text}
    </Typography>
  )
}

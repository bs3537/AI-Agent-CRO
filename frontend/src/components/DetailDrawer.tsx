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

// Faint placeholder for an empty list.
function EmptyRow({ text }: { text: string }) {
  return (
    <Typography variant="body2" sx={{ opacity: 0.5, py: 0.5 }}>
      {text}
    </Typography>
  )
}

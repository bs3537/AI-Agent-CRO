import { useEffect, useRef, useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Checkbox from '@mui/material/Checkbox'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import FormControlLabel from '@mui/material/FormControlLabel'
import IconButton from '@mui/material/IconButton'
import Link from '@mui/material/Link'
import List from '@mui/material/List'
import ListItem from '@mui/material/ListItem'
import ListItemText from '@mui/material/ListItemText'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import CloseIcon from '@mui/icons-material/Close'
import DeleteIcon from '@mui/icons-material/Delete'
import EventNoteIcon from '@mui/icons-material/EventNote'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import { api } from '../api'
import type { FileMeta, PositionDetail, PositionSummary } from '../types'
import CatalystDrawer, { CATALYST_DRAWER_WIDTH } from './CatalystDrawer'
import ThesisTargetLine from './ThesisTargetLine'

const MAX_THESIS_WORDS = 2000
const THESIS_WIDTH = 520

export type ThesisPackage = {
  ticker: string
  thesis: string
  files: File[]
  replaceFiles: boolean
}

export default function ThesisDrawer({
  ticker,
  position,
  onClose,
  onSavePackage,
  onChanged,
}: {
  ticker: string | null
  position: PositionSummary | null
  onClose: () => void
  onSavePackage: (pkg: ThesisPackage) => Promise<void>
  onChanged: () => void
}) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [detail, setDetail] = useState<PositionDetail | null>(null)
  const [thesis, setThesis] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [replaceFiles, setReplaceFiles] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [catalystOpen, setCatalystOpen] = useState(false)

  useEffect(() => {
    setError(null)
    setDetail(null)
    setFiles([])
    setReplaceFiles(true)
    setThesis(position?.thesis ?? '')
    setCatalystOpen(false)
    if (!ticker) return
    api
      .detail(ticker)
      .then((d) => {
        setDetail(d)
        setThesis(d.thesis)
      })
      .catch((e) => setError(String(e)))
  }, [ticker, position?.thesis])

  const wordCount = countWords(thesis)
  const tooLong = wordCount > MAX_THESIS_WORDS
  const isAiDraft = position?.is_ai_generated_thesis
    || (position?.thesis_source === 'ai_generated' && position?.thesis_status === 'draft')
  const preliminary = isAiDraft ? position?.preliminary_thesis : null

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? [])
    if (picked.length > 0) setFiles((current) => [...current, ...picked])
    if (inputRef.current) inputRef.current.value = ''
  }

  const removePicked = (index: number) => {
    setFiles((current) => current.filter((_f, i) => i !== index))
  }

  const removeCurrentFile = async (file: FileMeta) => {
    if (!ticker) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteFile(ticker, file.event_id)
      await api.recompute(ticker, true)
      const fresh = await api.detail(ticker)
      setDetail(fresh)
      onChanged()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!ticker || tooLong) return
    setBusy(true)
    setError(null)
    try {
      await onSavePackage({ ticker, thesis, files, replaceFiles })
      setFiles([])
      onClose()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Drawer
        anchor="right"
        variant="persistent"
        open={!!ticker}
        onClose={busy ? undefined : onClose}
        PaperProps={{
          sx: (theme) => ({
            width: { xs: '100vw', sm: THESIS_WIDTH },
            zIndex: theme.zIndex.drawer + 1,
            borderLeft: '1px solid',
            borderColor: 'divider',
            overflowY: 'auto',
            right: { xs: 0, md: catalystOpen ? `${CATALYST_DRAWER_WIDTH}px` : 0 },
            transition: `${theme.transitions.create(['transform', 'right'], {
              duration: theme.transitions.duration.enteringScreen,
            })} !important`,
          }),
        }}
      >
        <Box sx={{ p: { xs: 1.5, sm: 2 } }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between">
            <Typography variant="h6">
              {ticker} {isAiDraft ? 'preliminary thesis' : 'thesis'}
            </Typography>
            <IconButton onClick={onClose} size="small" disabled={busy}>
              <CloseIcon />
            </IconButton>
          </Stack>
          <Divider sx={{ my: 1.5 }} />

          {error && (
            <Typography color="error" variant="body2" sx={{ mb: 1.5 }}>
              {error}
            </Typography>
          )}

          <Stack spacing={2}>
            {isAiDraft && (
              <Box>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  AI-generated · {position?.thesis_generated_by ?? 'model'}
                  {position?.thesis_generated_at
                    ? ` · ${new Date(position.thesis_generated_at).toLocaleString()}`
                    : ''}
                </Typography>
                <Box sx={{ mt: 0.75 }}>
                  <ThesisTargetLine
                    target={position?.analyst_target ?? null}
                    isEtf={position?.is_etf ?? false}
                  />
                </Box>
              </Box>
            )}

            <TextField
              label={isAiDraft ? 'Preliminary thesis' : 'Thesis'}
              value={thesis}
              onChange={(e) => setThesis(e.target.value)}
              multiline
              minRows={12}
              maxRows={24}
              fullWidth
              error={tooLong}
              helperText={`${wordCount}/${MAX_THESIS_WORDS} words`}
            />

            {preliminary && preliminary.research_sources.length > 0 && (
              <Box>
                <Typography variant="subtitle2" sx={{ color: 'primary.main', mb: 0.5 }}>
                  Research sources
                </Typography>
                <Stack spacing={0.5}>
                  {preliminary.research_sources.map((source) => (
                    <Link
                      key={source.url}
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      variant="body2"
                      underline="hover"
                      sx={{ overflowWrap: 'anywhere' }}
                    >
                      {source.title}
                    </Link>
                  ))}
                </Stack>
              </Box>
            )}

            <Box>
              <input
                ref={inputRef}
                type="file"
                hidden
                multiple
                accept=".txt,.md,.markdown,.pdf,.docx"
                onChange={onPick}
              />
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<UploadFileIcon />}
                  disabled={busy}
                  onClick={() => inputRef.current?.click()}
                >
                  Add docs
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<EventNoteIcon />}
                  disabled={busy || !ticker}
                  onClick={() => setCatalystOpen(true)}
                >
                  Add / edit catalysts
                </Button>
                <FormControlLabel
                  control={
                    <Checkbox
                      size="small"
                      checked={replaceFiles}
                      onChange={(e) => setReplaceFiles(e.target.checked)}
                    />
                  }
                  label="Replace prior docs"
                />
              </Stack>
              {files.length > 0 && (
                <List dense disablePadding sx={{ mt: 1 }}>
                  {files.map((file, i) => (
                    <ListItem
                      key={`${file.name}-${i}`}
                      disableGutters
                      secondaryAction={
                        <IconButton edge="end" size="small" onClick={() => removePicked(i)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      }
                    >
                      <ListItemText primary={file.name} secondary={formatBytes(file.size)} />
                    </ListItem>
                  ))}
                </List>
              )}
            </Box>

            <Stack direction="row" spacing={1}>
              <Button
                variant="contained"
                onClick={() => void save()}
                disabled={busy || tooLong || !ticker}
              >
                {busy ? 'Saving...' : 'Save and recompute'}
              </Button>
              <Button onClick={onClose} disabled={busy}>
                Cancel
              </Button>
            </Stack>

            <Divider />

            <Box>
              <Typography variant="subtitle2" sx={{ color: 'primary.main', mb: 0.5 }}>
                Current docs ({detail?.files.length ?? 0})
              </Typography>
              <List dense disablePadding>
                {(detail?.files ?? []).map((file) => (
                  <ListItem
                    key={file.event_id}
                    disableGutters
                    secondaryAction={
                      <IconButton
                        edge="end"
                        size="small"
                        disabled={busy}
                        onClick={() => void removeCurrentFile(file)}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    }
                  >
                    <ListItemText
                      primary={file.filename}
                      secondary={`${file.content_type} - ${file.n_chars} chars`}
                    />
                  </ListItem>
                ))}
                {detail && detail.files.length === 0 && (
                  <Typography variant="body2" sx={{ opacity: 0.5 }}>
                    No uploaded documents.
                  </Typography>
                )}
                {!detail && !error && (
                  <Typography variant="body2" sx={{ opacity: 0.5 }}>
                    Loading...
                  </Typography>
                )}
              </List>
            </Box>
          </Stack>
        </Box>
      </Drawer>

      <CatalystDrawer
        ticker={catalystOpen ? ticker : null}
        onClose={() => setCatalystOpen(false)}
        onSaved={() => {
          onChanged()
        }}
      />
    </>
  )
}

function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0
}

function formatBytes(size: number): string {
  if (size >= 1_000_000) return `${(size / 1_000_000).toFixed(1)} MB`
  if (size >= 1_000) return `${(size / 1_000).toFixed(1)} KB`
  return `${size} B`
}

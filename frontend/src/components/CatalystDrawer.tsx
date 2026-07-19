import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import CloseIcon from '@mui/icons-material/Close'
import { api } from '../api'
import type { CatalystOutlookItem } from '../types'

const MAX_CATALYST_WORDS = 400
const DRAWER_WIDTH = 460

function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0
}

function itemsToText(items: CatalystOutlookItem[]): string {
  if (items.length === 0) return ''
  return items
    .map((item) => {
      const parts = [item.date_label, item.label]
      if (item.type && item.type !== 'other') parts.push(`(${item.type.replace(/_/g, ' ')})`)
      return parts.join(': ')
    })
    .join('\n')
}

export default function CatalystDrawer({
  ticker,
  onClose,
  onSaved,
}: {
  ticker: string | null
  onClose: () => void
  onSaved: () => void
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setText('')
    setError(null)
    if (!ticker) return
    setLoading(true)
    api
      .detail(ticker)
      .then((d) => {
        setText(itemsToText(d.catalyst_outlook))
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [ticker])

  const wordCount = countWords(text)
  const tooLong = wordCount > MAX_CATALYST_WORDS

  const save = async () => {
    if (!ticker || tooLong) return
    setBusy(true)
    setError(null)
    try {
      await api.saveCatalysts(ticker, text)
      onSaved()
      onClose()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer
      anchor="right"
      variant="persistent"
      open={Boolean(ticker)}
      PaperProps={{
        sx: (theme) => ({
          width: { xs: '100vw', sm: DRAWER_WIDTH },
          zIndex: theme.zIndex.drawer + 2,
          borderLeft: '1px solid',
          borderColor: 'divider',
        }),
      }}
    >
      <Box sx={{ p: { xs: 1.5, sm: 2 } }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Typography variant="h6">
            {ticker} catalysts
          </Typography>
          <IconButton onClick={onClose} size="small" disabled={busy}>
            <CloseIcon />
          </IconButton>
        </Stack>
        <Divider sx={{ my: 1.5 }} />

        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 1.5 }}>
          Edit or add catalysts below. Each line is one catalyst (e.g.&nbsp;
          <em>Q4 2025: FDA decision on NDA approval (regulatory)</em>). These
          are saved to the same source displayed in the positions table.
        </Typography>

        {error && (
          <Typography color="error" variant="body2" sx={{ mb: 1.5 }}>
            {error}
          </Typography>
        )}

        <TextField
          label="Catalysts"
          value={loading ? 'Loading...' : text}
          onChange={(e) => setText(e.target.value)}
          multiline
          minRows={10}
          maxRows={20}
          fullWidth
          disabled={busy || loading}
          error={tooLong}
          helperText={`${wordCount}/${MAX_CATALYST_WORDS} words`}
          placeholder={'Q4 2025: FDA PDUFA date for XYZ (regulatory)\nOct 2025: Phase 3 data readout (clinical_data)\n2026-Q1: Earnings release (earnings)'}
          sx={{ mb: 2 }}
        />

        <Stack direction="row" spacing={1}>
          <Button
            variant="contained"
            onClick={() => void save()}
            disabled={busy || tooLong || !ticker || loading}
          >
            {busy ? 'Saving...' : 'Save catalysts'}
          </Button>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
        </Stack>
      </Box>
    </Drawer>
  )
}

export { DRAWER_WIDTH as CATALYST_DRAWER_WIDTH }

import { useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import CloseIcon from '@mui/icons-material/Close'
import type { ManualPositionPayload } from '../types'

const MAX_THESIS_WORDS = 2000

export default function AddPositionDrawer({
  open,
  onClose,
  onAdd,
}: {
  open: boolean
  onClose: () => void
  onAdd: (payload: ManualPositionPayload) => Promise<void>
}) {
  const [ticker, setTicker] = useState('')
  const [weightPct, setWeightPct] = useState('')
  const [thesis, setThesis] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const wordCount = countWords(thesis)
  const parsedWeight = Number(weightPct)
  const invalidWeight = !Number.isFinite(parsedWeight) || parsedWeight <= 0 || parsedWeight > 100
  const invalid = !ticker.trim() || invalidWeight || !thesis.trim() || wordCount > MAX_THESIS_WORDS

  const reset = () => {
    setTicker('')
    setWeightPct('')
    setThesis('')
    setError(null)
  }

  const close = () => {
    if (busy) return
    reset()
    onClose()
  }

  const submit = async () => {
    if (invalid) return
    setBusy(true)
    setError(null)
    try {
      await onAdd({
        ticker: ticker.trim().toUpperCase(),
        portfolio_weight_pct: parsedWeight,
        thesis,
      })
      reset()
      onClose()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer anchor="right" open={open} onClose={busy ? undefined : close}>
      <Box sx={{ width: { xs: 360, sm: 520 }, p: 2 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Typography variant="h6">Add position</Typography>
          <IconButton onClick={close} size="small" disabled={busy}>
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
          <TextField
            label="Stock symbol"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            disabled={busy}
            fullWidth
            autoFocus
          />
          <TextField
            label="Portfolio weight (%)"
            value={weightPct}
            onChange={(e) => setWeightPct(e.target.value)}
            disabled={busy}
            error={Boolean(weightPct) && invalidWeight}
            helperText="Example: 2.5 means 2.5% NAV"
            inputProps={{ inputMode: 'decimal' }}
            fullWidth
          />
          <TextField
            label="Original buying thesis"
            value={thesis}
            onChange={(e) => setThesis(e.target.value)}
            disabled={busy}
            multiline
            minRows={12}
            maxRows={24}
            error={wordCount > MAX_THESIS_WORDS}
            helperText={`${wordCount}/${MAX_THESIS_WORDS} words`}
            fullWidth
          />
          <Stack direction="row" spacing={1}>
            <Button variant="contained" disabled={busy || invalid} onClick={() => void submit()}>
              {busy ? 'Adding and analyzing...' : 'Add and analyze'}
            </Button>
            <Button disabled={busy} onClick={close}>
              Cancel
            </Button>
          </Stack>
        </Stack>
      </Box>
    </Drawer>
  )
}

function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0
}

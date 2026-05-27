import { useEffect, useRef, useState } from 'react'
import Box from '@mui/material/Box'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

// Inline thesis editor with debounced autosave. Edits flush to onSave ~900ms
// after typing stops; the little status line reflects saving/saved/error.
export default function ThesisEditor({
  ticker,
  thesis,
  onSave,
}: {
  ticker: string
  thesis: string
  onSave: (thesis: string) => Promise<void>
}) {
  const [value, setValue] = useState(thesis)
  const [state, setState] = useState<SaveState>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastSaved = useRef(thesis)

  // Re-sync local text when the upstream thesis changes (e.g. after a poll)
  // and there is no unsaved local edit pending.
  useEffect(() => {
    if (state !== 'saving' && value === lastSaved.current) {
      setValue(thesis)
      lastSaved.current = thesis
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thesis])

  // Debounce-save on change.
  const onChange = (next: string) => {
    setValue(next)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void flush(next), 900)
  }

  // Persist the current value if it differs from what was last saved.
  const flush = async (next: string) => {
    if (next === lastSaved.current) return
    setState('saving')
    try {
      await onSave(next)
      lastSaved.current = next
      setState('saved')
    } catch {
      setState('error')
    }
  }

  return (
    <Box>
      <TextField
        label={`Thesis — ${ticker}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => void flush(value)}
        multiline
        minRows={2}
        maxRows={8}
        fullWidth
        size="small"
      />
      <Typography variant="caption" sx={{ opacity: 0.6 }}>
        {state === 'saving' && 'Saving…'}
        {state === 'saved' && 'Saved'}
        {state === 'error' && 'Save failed — retry'}
        {state === 'idle' && 'Autosaves on pause'}
      </Typography>
    </Box>
  )
}

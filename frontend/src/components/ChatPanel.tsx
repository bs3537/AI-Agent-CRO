import { useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import Divider from '@mui/material/Divider'
import IconButton from '@mui/material/IconButton'
import InputAdornment from '@mui/material/InputAdornment'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import AttachFileIcon from '@mui/icons-material/AttachFile'
import CloseIcon from '@mui/icons-material/Close'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import SearchIcon from '@mui/icons-material/Search'
import SendIcon from '@mui/icons-material/Send'
import { api } from '../api'
import type { ChatHistoryMessage } from '../types'

const STORAGE_KEY = 'ai-cro-chat-history-v1'
const ACCEPT = [
  '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.jsonl',
  '.pdf', '.docx', '.xlsx', '.xlsm', '.xls', '.png', '.jpg', '.jpeg', '.webp', '.gif',
].join(',')

interface StoredChatMessage extends ChatHistoryMessage {
  id: string
  created_at: string
  model_used?: string
  attachments?: string[]
}

export default function ChatPanel({
  width,
  onWidthChange,
  onClose,
}: {
  width: number
  onWidthChange: (width: number) => void
  onClose: () => void
}) {
  const [messages, setMessages] = useState<StoredChatMessage[]>(loadHistory)
  const [search, setSearch] = useState('')
  const [draft, setDraft] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [sending, setSending] = useState(false)
  const [thinkingSecs, setThinkingSecs] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const scrollerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-200)))
  }, [messages])

  useEffect(() => {
    scrollerRef.current?.scrollTo({ top: scrollerRef.current.scrollHeight })
  }, [messages, sending])

  useEffect(() => {
    if (!sending) { setThinkingSecs(0); return }
    setThinkingSecs(0)
    const id = setInterval(() => setThinkingSecs((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [sending])

  const visibleMessages = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return messages
    return messages.filter(
      (m) =>
        m.content.toLowerCase().includes(q) ||
        (m.attachments ?? []).some((name) => name.toLowerCase().includes(q)),
    )
  }, [messages, search])

  const send = async () => {
    const text = draft.trim()
    if (!text || sending) return
    const userMessage: StoredChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
      attachments: files.map((f) => f.name),
    }
    const historyForApi = messages.map(({ role, content }) => ({ role, content })).slice(-24)
    setMessages((prev) => [...prev, userMessage])
    setDraft('')
    setSending(true)
    setError(null)
    try {
      const res = await api.chat({ message: text, history: historyForApi, files })
      const assistantMessage: StoredChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: res.answer,
        created_at: new Date().toISOString(),
        model_used: res.model_used,
        attachments: res.attachments.map((a) => `${a.filename} (${a.parser})`),
      }
      setMessages((prev) => [...prev, assistantMessage])
      setFiles([])
    } catch (e) {
      setError(String(e))
    } finally {
      setSending(false)
    }
  }

  const onPickFiles = (picked: FileList | null) => {
    if (!picked) return
    setFiles((prev) => [...prev, ...Array.from(picked)])
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const clearHistory = () => {
    setMessages([])
    localStorage.removeItem(STORAGE_KEY)
  }

  return (
    <Box
      sx={{
        width: { xs: '100vw', md: width },
        flexShrink: 0,
        borderLeft: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.paper',
        position: 'sticky',
        top: { xs: 56, sm: 64 },
        height: { xs: 'calc(100vh - 56px)', sm: 'calc(100vh - 64px)' },
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <ResizeHandle width={width} onWidthChange={onWidthChange} />
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 1 }}>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            AI CRO Chat
          </Typography>
          <Typography variant="caption" sx={{ opacity: 0.6 }}>
            Saved dashboard data + uploaded files
          </Typography>
        </Box>
        <Tooltip title="Clear chat history">
          <IconButton size="small" onClick={clearHistory}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <IconButton size="small" onClick={onClose}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Stack>
      <Box sx={{ px: 1.5, pb: 1 }}>
        <TextField
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          size="small"
          fullWidth
          placeholder="Search chat history"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />
      </Box>
      <Divider />
      <Box ref={scrollerRef} sx={{ flexGrow: 1, overflowY: 'auto', p: 1.5 }}>
        {visibleMessages.length === 0 && (
          <Typography variant="body2" sx={{ opacity: 0.55, mt: 2 }}>
            Ask about a holding, grade change, thesis drift, stored news, or portfolio risk.
          </Typography>
        )}
        <Stack spacing={1.25}>
          {visibleMessages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {sending && (
            <Typography variant="body2" sx={{ opacity: 0.55 }}>
              Thinking… {thinkingSecs}s
            </Typography>
          )}
        </Stack>
      </Box>
      <Divider />
      <Box sx={{ p: 1.5 }}>
        {error && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {error}
          </Alert>
        )}
        {files.length > 0 && (
          <Stack direction="row" gap={0.75} flexWrap="wrap" sx={{ mb: 1 }}>
            {files.map((file, i) => (
              <Chip
                key={`${file.name}-${i}`}
                size="small"
                label={file.name}
                onDelete={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
              />
            ))}
          </Stack>
        )}
        <TextField
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          fullWidth
          multiline
          minRows={3}
          maxRows={7}
          placeholder="Ask the AI CRO…"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void send()
            }
          }}
        />
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mt: 1 }}>
          <Box>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              accept={ACCEPT}
              onChange={(e) => onPickFiles(e.target.files)}
            />
            <Button
              size="small"
              variant="outlined"
              startIcon={<AttachFileIcon />}
              onClick={() => fileInputRef.current?.click()}
              disabled={sending}
            >
              Attach
            </Button>
          </Box>
          <Button
            size="small"
            variant="contained"
            endIcon={<SendIcon />}
            disabled={!draft.trim() || sending}
            onClick={() => void send()}
          >
            Send
          </Button>
        </Stack>
      </Box>
    </Box>
  )
}

function MessageBubble({ message }: { message: StoredChatMessage }) {
  const isUser = message.role === 'user'
  return (
    <Box sx={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <Box
        sx={{
          maxWidth: '94%',
          border: '1px solid',
          borderColor: isUser ? 'primary.main' : 'divider',
          bgcolor: isUser ? 'rgba(255,106,0,0.10)' : 'rgba(255,255,255,0.03)',
          borderRadius: 1,
          px: 1.25,
          py: 1,
        }}
      >
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
          {message.content}
        </Typography>
        {(message.attachments?.length || message.model_used) && (
          <Typography variant="caption" sx={{ opacity: 0.55, display: 'block', mt: 0.75 }}>
            {[message.model_used, ...(message.attachments ?? [])].filter(Boolean).join(' · ')}
          </Typography>
        )}
      </Box>
    </Box>
  )
}

function ResizeHandle({
  width,
  onWidthChange,
}: {
  width: number
  onWidthChange: (width: number) => void
}) {
  const start = useRef<{ x: number; width: number } | null>(null)

  const onMouseDown = (e: ReactMouseEvent) => {
    start.current = { x: e.clientX, width }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => {
      if (!start.current) return
      const next = Math.min(760, Math.max(360, start.current.width - (ev.clientX - start.current.x)))
      onWidthChange(next)
    }
    const onUp = () => {
      start.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <Box
      onMouseDown={onMouseDown}
      sx={{
        position: 'absolute',
        left: -3,
        top: 0,
        bottom: 0,
        width: 6,
        cursor: 'col-resize',
        display: { xs: 'none', md: 'block' },
        '&:hover': { bgcolor: 'primary.main', opacity: 0.8 },
      }}
    />
  )
}

function loadHistory(): StoredChatMessage[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

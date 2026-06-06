import { useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import IconButton from '@mui/material/IconButton'
import InputAdornment from '@mui/material/InputAdornment'
import List from '@mui/material/List'
import ListItemButton from '@mui/material/ListItemButton'
import ListItemText from '@mui/material/ListItemText'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import AttachFileIcon from '@mui/icons-material/AttachFile'
import CloseIcon from '@mui/icons-material/Close'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import AddCommentIcon from '@mui/icons-material/AddComment'
import HistoryIcon from '@mui/icons-material/History'
import SearchIcon from '@mui/icons-material/Search'
import SendIcon from '@mui/icons-material/Send'
import { api } from '../api'
import type { ChatHistoryMessage, ChatQueuedResponse, ChatResponse, ChatSubmitResponse } from '../types'

// ── Storage keys ──────────────────────────────────────────────────────────────
const LEGACY_KEY = 'ai-cro-chat-history-v1'
const SESSIONS_KEY = 'ai-cro-chat-sessions-v2'

const ACCEPT = [
  '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.jsonl',
  '.pdf', '.docx', '.xlsx', '.xlsm', '.xls', '.png', '.jpg', '.jpeg', '.webp', '.gif',
].join(',')

// ── Types ─────────────────────────────────────────────────────────────────────
interface StoredChatMessage extends ChatHistoryMessage {
  id: string
  created_at: string
  model_used?: string
  attachments?: string[]
}

interface ChatSession {
  id: string
  name: string
  created_at: string
  messages: StoredChatMessage[]
}

interface SessionStore {
  sessions: ChatSession[]
  currentId: string
}

// ── Persistence helpers ───────────────────────────────────────────────────────
function makeSessionName(messages: StoredChatMessage[], created_at: string): string {
  const first = messages.find((m) => m.role === 'user')
  const snippet = first ? first.content.slice(0, 48) + (first.content.length > 48 ? '…' : '') : 'New chat'
  const date = new Date(created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  return `${date} — ${snippet}`
}

function newSession(): ChatSession {
  return { id: crypto.randomUUID(), name: 'New chat', created_at: new Date().toISOString(), messages: [] }
}

function loadStore(): SessionStore {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as SessionStore
      if (parsed.sessions?.length && parsed.currentId) return parsed
    }
    // Migrate legacy single-history to session store
    const legacyRaw = localStorage.getItem(LEGACY_KEY)
    const legacy: StoredChatMessage[] = legacyRaw ? JSON.parse(legacyRaw) : []
    const seed = newSession()
    if (Array.isArray(legacy) && legacy.length) {
      seed.messages = legacy
      seed.created_at = legacy[0].created_at ?? seed.created_at
      seed.name = makeSessionName(legacy, seed.created_at)
    }
    return { sessions: [seed], currentId: seed.id }
  } catch {
    const seed = newSession()
    return { sessions: [seed], currentId: seed.id }
  }
}

function saveStore(store: SessionStore) {
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(store))
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function ChatPanel({
  width,
  onWidthChange,
  onClose,
}: {
  width: number
  onWidthChange: (width: number) => void
  onClose: () => void
}) {
  const [store, setStore] = useState<SessionStore>(loadStore)
  const [search, setSearch] = useState('')
  const [draft, setDraft] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [sending, setSending] = useState(false)
  const [thinkingSecs, setThinkingSecs] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historySearch, setHistorySearch] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const scrollerRef = useRef<HTMLDivElement | null>(null)

  const currentSession = store.sessions.find((s) => s.id === store.currentId) ?? store.sessions[0]
  const messages = currentSession?.messages ?? []

  // Persist on every store change
  useEffect(() => { saveStore(store) }, [store])

  // Scroll to bottom on new message
  useEffect(() => {
    scrollerRef.current?.scrollTo({ top: scrollerRef.current.scrollHeight })
  }, [messages, sending])

  // Thinking timer
  useEffect(() => {
    if (!sending) { setThinkingSecs(0); return }
    setThinkingSecs(0)
    const id = setInterval(() => setThinkingSecs((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [sending])

  // Filtered messages in current session
  const visibleMessages = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return messages
    return messages.filter(
      (m) =>
        m.content.toLowerCase().includes(q) ||
        (m.attachments ?? []).some((name) => name.toLowerCase().includes(q)),
    )
  }, [messages, search])

  // Sessions list filtered by history search
  const visibleSessions = useMemo(() => {
    const q = historySearch.trim().toLowerCase()
    if (!q) return [...store.sessions].sort((a, b) => b.created_at.localeCompare(a.created_at))
    return [...store.sessions]
      .filter((s) =>
        s.name.toLowerCase().includes(q) ||
        s.messages.some((m) => m.content.toLowerCase().includes(q)),
      )
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
  }, [store.sessions, historySearch])

  // Update the current session's messages
  const updateCurrentMessages = (updater: (prev: StoredChatMessage[]) => StoredChatMessage[]) => {
    setStore((prev) => {
      const sessions = prev.sessions.map((s) => {
        if (s.id !== prev.currentId) return s
        const next = updater(s.messages)
        return {
          ...s,
          messages: next,
          name: next.length ? makeSessionName(next, s.created_at) : 'New chat',
        }
      })
      return { ...prev, sessions }
    })
  }

  const startNewChat = () => {
    const session = newSession()
    setStore((prev) => ({
      sessions: [session, ...prev.sessions],
      currentId: session.id,
    }))
    setSearch('')
    setDraft('')
    setFiles([])
    setError(null)
    setHistoryOpen(false)
  }

  const switchSession = (id: string) => {
    setStore((prev) => ({ ...prev, currentId: id }))
    setSearch('')
    setDraft('')
    setFiles([])
    setError(null)
    setHistoryOpen(false)
  }

  const deleteSession = (id: string) => {
    setStore((prev) => {
      const sessions = prev.sessions.filter((s) => s.id !== id)
      if (sessions.length === 0) {
        const seed = newSession()
        return { sessions: [seed], currentId: seed.id }
      }
      const currentId = prev.currentId === id
        ? sessions[0].id
        : prev.currentId
      return { sessions, currentId }
    })
  }

  const clearCurrentChat = () => {
    updateCurrentMessages(() => [])
  }

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
    updateCurrentMessages((prev) => [...prev, userMessage])
    setDraft('')
    setSending(true)
    setError(null)
    try {
      const initial = await api.chat({ message: text, history: historyForApi, files })
      let res: ChatResponse
      if (isQueuedChatResponse(initial)) {
        res = await waitForQueuedChat(initial.request_id)
      } else {
        res = initial
      }
      const assistantMessage: StoredChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: res.answer,
        created_at: new Date().toISOString(),
        model_used: res.model_used,
        attachments: res.attachments.map((a) => `${a.filename} (${a.parser})`),
      }
      updateCurrentMessages((prev) => [...prev, assistantMessage])
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

      {/* ── Header ── */}
      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ px: 1.5, py: 1 }}>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            AI CRO Chat
          </Typography>
          <Typography variant="caption" sx={{ opacity: 0.6 }} noWrap>
            {currentSession?.name ?? 'New chat'}
          </Typography>
        </Box>
        <Tooltip title="New chat">
          <IconButton size="small" onClick={startNewChat}>
            <AddCommentIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Chat history">
          <IconButton size="small" onClick={() => setHistoryOpen(true)}>
            <HistoryIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Clear current chat">
          <IconButton size="small" onClick={clearCurrentChat}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <IconButton size="small" onClick={onClose}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Stack>

      {/* ── In-chat search ── */}
      <Box sx={{ px: 1.5, pb: 1 }}>
        <TextField
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          size="small"
          fullWidth
          placeholder="Search this chat"
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

      {/* ── Messages ── */}
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

      {/* ── Compose area ── */}
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

      {/* ── History Drawer ── */}
      <Drawer
        anchor="right"
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        PaperProps={{ sx: { width: 320, display: 'flex', flexDirection: 'column' } }}
      >
        <Stack direction="row" alignItems="center" sx={{ px: 2, pt: 2, pb: 1 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700, flexGrow: 1 }}>
            Chat History
          </Typography>
          <IconButton size="small" onClick={() => setHistoryOpen(false)}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>
        <Box sx={{ px: 2, pb: 1.5 }}>
          <TextField
            value={historySearch}
            onChange={(e) => setHistorySearch(e.target.value)}
            size="small"
            fullWidth
            placeholder="Search sessions…"
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
        <Box sx={{ flexGrow: 1, overflowY: 'auto' }}>
          {visibleSessions.length === 0 && (
            <Typography variant="body2" sx={{ opacity: 0.55, p: 2 }}>
              No sessions match.
            </Typography>
          )}
          <List dense disablePadding>
            {visibleSessions.map((s) => (
              <Box key={s.id}>
                <ListItemButton
                  selected={s.id === store.currentId}
                  onClick={() => switchSession(s.id)}
                  sx={{ pr: 1 }}
                >
                  <ListItemText
                    primary={s.name}
                    secondary={`${s.messages.length} messages · ${new Date(s.created_at).toLocaleDateString()}`}
                    primaryTypographyProps={{ noWrap: true, variant: 'body2' }}
                    secondaryTypographyProps={{ variant: 'caption' }}
                  />
                  <Tooltip title="Delete session">
                    <IconButton
                      size="small"
                      onClick={(e) => { e.stopPropagation(); deleteSession(s.id) }}
                      sx={{ ml: 0.5, opacity: 0.5, '&:hover': { opacity: 1 } }}
                    >
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </ListItemButton>
                <Divider component="li" />
              </Box>
            ))}
          </List>
        </Box>
        <Divider />
        <Box sx={{ p: 2 }}>
          <Button
            fullWidth
            variant="outlined"
            startIcon={<AddCommentIcon />}
            onClick={startNewChat}
          >
            New Chat
          </Button>
        </Box>
      </Drawer>
    </Box>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function isQueuedChatResponse(res: ChatSubmitResponse): res is ChatQueuedResponse {
  return 'scheduled' in res && res.scheduled === true
}

async function waitForQueuedChat(requestId: string): Promise<ChatResponse> {
  const deadline = Date.now() + 10 * 60 * 1000
  let lastStatus: string = 'queued'
  while (Date.now() < deadline) {
    await sleep(1500)
    const status = await api.chatStatus(requestId)
    lastStatus = status.status
    if (status.status === 'succeeded' && status.result) return status.result
    if (status.status === 'failed') {
      throw new Error(status.error || 'VPS Codex chat request failed')
    }
  }
  throw new Error(`VPS Codex chat request is still ${lastStatus}; try again in a minute.`)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
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

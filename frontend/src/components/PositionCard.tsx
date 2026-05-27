import { useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import RefreshIcon from '@mui/icons-material/Refresh'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import type { PositionSummary } from '../types'
import { VERDICT_HEX } from '../theme'
import DecisionChip from './DecisionChip'
import PnL from './PnL'
import ThesisEditor from './ThesisEditor'
import FileUpload from './FileUpload'

// One position tile: header (ticker + decision chip), economics row, the
// decision note + drivers, the inline thesis editor, and the action row
// (upload, recompute, details). All mutations call back up to App.
export default function PositionCard({
  pos,
  onSaveThesis,
  onUpload,
  onRecompute,
  onOpenDetail,
}: {
  pos: PositionSummary
  onSaveThesis: (ticker: string, thesis: string) => Promise<void>
  onUpload: (ticker: string, file: File) => Promise<void>
  onRecompute: (ticker: string) => Promise<void>
  onOpenDetail: (ticker: string) => void
}) {
  const [recomputing, setRecomputing] = useState(false)
  const accent = pos.decision ? VERDICT_HEX[pos.decision.color] ?? '#888' : '#444'

  // Recompute this position's decision, showing a busy label meanwhile.
  const recompute = async () => {
    setRecomputing(true)
    try {
      await onRecompute(pos.ticker)
    } finally {
      setRecomputing(false)
    }
  }

  return (
    <Card sx={{ borderLeft: `4px solid ${accent}` }}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Box>
            <Typography variant="h6">{pos.ticker}</Typography>
            <Typography variant="caption" sx={{ opacity: 0.7 }}>
              {pos.company_name ?? '—'} · tier {pos.conviction_tier} · {pos.stage}
            </Typography>
          </Box>
          <DecisionChip decision={pos.decision} />
        </Stack>

        <Stack direction="row" spacing={2} alignItems="center" sx={{ mt: 1 }}>
          <PnL openPnl={pos.open_pnl} pnlPct={pos.pnl_pct} />
          <Chip size="small" variant="outlined" label={`${(pos.pct_nav * 100).toFixed(1)}% NAV`} />
          {pos.nearest_catalyst_days !== null && (
            <Chip
              size="small"
              variant="outlined"
              color={pos.has_overdue_catalyst ? 'error' : 'default'}
              label={`catalyst ${pos.nearest_catalyst_days}d`}
            />
          )}
        </Stack>

        {pos.decision && (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-line' }}>
              {pos.decision.note}
            </Typography>
            {pos.decision.drivers.length > 0 && (
              <Box sx={{ mt: 1 }}>
                {pos.decision.drivers.map((d, i) => (
                  <Chip key={i} size="small" variant="outlined" label={d} sx={{ mr: 0.5, mb: 0.5 }} />
                ))}
              </Box>
            )}
            <Typography variant="caption" sx={{ opacity: 0.5 }}>
              {pos.decision.model_used} · conf {(pos.decision.confidence * 100).toFixed(0)}% ·{' '}
              {new Date(pos.decision.decided_at).toLocaleString()}
            </Typography>
          </Box>
        )}

        <Box sx={{ mt: 1.5 }}>
          <ThesisEditor
            ticker={pos.ticker}
            thesis={pos.thesis}
            onSave={(thesis) => onSaveThesis(pos.ticker, thesis)}
          />
        </Box>

        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} alignItems="center" flexWrap="wrap">
          <FileUpload onUpload={(file) => onUpload(pos.ticker, file)} />
          <Button
            size="small"
            variant="contained"
            startIcon={<RefreshIcon />}
            disabled={recomputing}
            onClick={() => void recompute()}
          >
            {recomputing ? 'Recomputing…' : 'Recompute'}
          </Button>
          <Button
            size="small"
            startIcon={<InfoOutlinedIcon />}
            onClick={() => onOpenDetail(pos.ticker)}
          >
            Details{pos.n_files > 0 ? ` (${pos.n_files} files)` : ''}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  )
}

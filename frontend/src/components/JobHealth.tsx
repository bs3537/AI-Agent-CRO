import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Tooltip from '@mui/material/Tooltip'
import { api } from '../api'
import type { JobStatus } from '../types'

const POLL_MS = 60_000

// Compact "5h ago" style age for a timestamp.
function ago(iso: string | null): string {
  if (!iso) return 'never'
  const mins = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000))
  if (mins < 60) return `${mins}m ago`
  if (mins < 48 * 60) return `${Math.round(mins / 60)}h ago`
  return `${Math.round(mins / 1440)}d ago`
}

// One chip per scheduled data job: green when healthy, red when the last run
// failed, amber when no success has landed within the expected window.
export default function JobHealth() {
  const [jobs, setJobs] = useState<JobStatus[]>([])

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .jobs()
        .then((res) => alive && setJobs(res.jobs))
        .catch(() => undefined)
    void load()
    const timer = window.setInterval(load, POLL_MS)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  if (jobs.length === 0) return null
  return (
    <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center', mb: 2 }}>
      {jobs.map((job) => {
        const failed = job.ok === false
        const color = failed ? 'error' : job.overdue ? 'warning' : 'success'
        const state = failed ? 'failed' : job.overdue ? 'overdue' : 'ok'
        return (
          <Tooltip
            key={job.job}
            title={`${job.detail ?? 'No run recorded yet'} · last run ${ago(job.last_run_at)}`}
          >
            <Chip
              size="small"
              variant="outlined"
              color={color}
              label={`${job.label}: ${state} · ${ago(job.last_success_at)}`}
            />
          </Tooltip>
        )
      })}
    </Box>
  )
}

# Deploying SMA Monitor (Hermes runner + Replit dashboard)

Production is split across two roles:

- **Replit dashboard**: FastAPI + React UI only. It reads/writes Turso and
  enqueues manual runner requests. It does not hold Codex auth or email secrets.
- **Hermes/VPS runner**: trusted Linux VM with Codex CLI login, source-data
  secrets, Resend, scheduled jobs, and the queue processor.

## Units in this directory

| Unit | Type | Role |
|------|------|------|
| `sma-api.service` | always-on | Optional VPS FastAPI backend + built dashboard at `/` |
| `sma-runner.service` | always-on | Hermes queue processor for Replit manual requests |
| `sma-thesis-recompute.service` + `.timer` | oneshot @ 06:00 ET | smart refresh + Codex thesis recompute |
| `sma-thesis-email.service` + `.timer` | oneshot @ 09:15 ET | send morning thesis email |
| `sma-collect.service` + `.timer` | oneshot @ 18:00 ET | positions, news, score, red-team, decide |
| `sma-dispatch.service` + `.timer` | oneshot @ 21:00 ET | assemble + send evening digest |
| `sma-monitor.service` | always-on | **alternative** scheduler: one run-loop covering the daily firings |

**Pick one scheduler:** either the timer units **or** the
`sma-monitor.service` run-loop — never both, or each firing runs twice.

- **Timers** (recommended on systemd ≥ 252): per-run journald logs,
  `Persistent=true` catches a firing missed during downtime. They use an
  `OnCalendar=… America/New_York` suffix, which needs systemd ≥ 252.
- **Run-loop** (`sma-monitor.service`): resolves ET via Python `zoneinfo`, so
  it works on any systemd version (or use it where you'd rather have one
  long-lived process). `python -m sma_monitor.orchestrator install-cron` emits
  an equivalent crontab snippet for non-systemd hosts.

## Replit dashboard secrets

Set these in Replit deployment secrets:

```bash
SMA_DEPLOYMENT_ROLE=dashboard
TURSO_DATABASE_URL=libsql://...
TURSO_AUTH_TOKEN=...
DATA_ROOT=/tmp/ai-cro-data
LOG_LEVEL=INFO
```

Do not set `CODEX_HOME`, `RESEND_API_KEY`, SMTP secrets, or Codex auth on Replit.

## Hermes one-time host setup

```bash
# 1. App at /opt/sma-monitor, owned by a dedicated 'sma' user.
sudo useradd --system --home /opt/sma-monitor --shell /usr/sbin/nologin sma
sudo mkdir -p /opt/sma-monitor && sudo chown sma:sma /opt/sma-monitor
# (clone the repo there, or rsync it)

# 2. Python venv + the package.
sudo -u sma python3 -m venv /opt/sma-monitor/.venv
sudo -u sma /opt/sma-monitor/.venv/bin/pip install -e /opt/sma-monitor

# 3. Secrets. Copy .env.example → .env, fill it in, lock it down.
sudo -u sma cp /opt/sma-monitor/.env.example /opt/sma-monitor/.env
sudo -u sma chmod 600 /opt/sma-monitor/.env   # then edit

# Required Hermes .env role:
# SMA_DEPLOYMENT_ROLE=runner
# TURSO_DATABASE_URL=...
# TURSO_AUTH_TOKEN=...
# RESEND_API_KEY=...
# RESEND_EMAIL_FROM=AI CRO Risk Desk <verified@yourdomain.com>
# RESEND_EMAIL_TO=...
# CODEX_HOME=/opt/sma-monitor/.codex

# 4. LLM auth — Codex subscription login (NO API key). Run as the 'sma' user
#    so the token lands in that user's ~/.codex/auth.json (= $CODEX_HOME).
sudo -u sma mkdir -p /opt/sma-monitor/.codex
sudo -u sma CODEX_HOME=/opt/sma-monitor/.codex codex login --device-auth
#    Without a Codex login the pipeline still runs — it falls back to the
#    deterministic heuristics.

# 5. Build the dashboard so the API can serve it (needs Node ≥ 18).
cd /opt/sma-monitor/frontend && npm install && npm run build   # → frontend/dist

# 6. Bootstrap the data dir + DB once (the units also do this via ExecStartPre).
sudo -u sma /opt/sma-monitor/.venv/bin/python -m sma_monitor
```

## Install the units

```bash
sudo cp /opt/sma-monitor/systemd/*.service /opt/sma-monitor/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# API (always-on):
sudo systemctl enable --now sma-api

# Scheduler — EITHER the three timers …
sudo systemctl enable --now sma-runner
sudo systemctl enable --now sma-thesis-recompute.timer sma-thesis-email.timer sma-collect.timer sma-dispatch.timer
# … OR the single run-loop (not both):
# sudo systemctl enable --now sma-monitor
```

## Operate

```bash
systemctl list-timers 'sma-*'              # next firing times
journalctl -u sma-runner -f                # Hermes runner queue logs
journalctl -u sma-api -f                   # API logs
journalctl -u sma-collect --since today    # a batch run's logs
sudo systemctl start sma-thesis-email.service   # fire a step now (manual)
sudo systemctl start sma-thesis-recompute.service
/opt/sma-monitor/.venv/bin/python -m sma_monitor.orchestrator runner-requests
```

## Reaching the dashboard

`sma-api.service` binds **127.0.0.1:8000** on purpose — the API has **no
authentication**. Reach it via an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 vm`)
or put a reverse proxy (nginx/Caddy) with TLS + auth in front. Do **not** edit
the unit to bind `0.0.0.0` on a public host.

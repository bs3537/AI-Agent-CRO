#!/usr/bin/env bash
# Cron wrapper for the SMA thesis-drift monitor.
#
# cron (and systemd) run with a minimal environment, so this script restores
# everything the LLM path needs that an interactive shell has but a scheduler
# does not: Node + the Codex CLI on PATH, the host's Codex device-auth login
# (CODEX_HOME), an absolute DATA_ROOT, and the W9 concurrency knob. Without
# these the app silently falls back to its heuristics instead of real Codex.
#
# Usage: sma_cron.sh <collect|dispatch|thesis-email|selfcheck>
set -euo pipefail

# --- Environment the scheduler doesn't provide -------------------------------
REPO="/home/bhavy/AI_Agent_CRO"
export PATH="/home/bhavy/.local/node/bin:${PATH:-/usr/bin:/bin}"   # codex CLI + node runtime
export SMA_CODEX_BIN="/home/bhavy/.local/node/bin/codex"
export CODEX_HOME="/home/bhavy/.codex"              # holds auth.json from `codex login`
export DATA_ROOT="$REPO/data"                       # live DB / sidecars / archived outputs
export SMA_LLM_CONCURRENCY="${SMA_LLM_CONCURRENCY:-4}"
PY="$REPO/.venv/bin/python"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"

CMD="${1:-}"

# --- selfcheck: prove the scheduler env can reach the real Codex provider ----
# Spends nothing and writes nothing — just reports provider availability.
if [ "$CMD" = "selfcheck" ]; then
  cd "$REPO"
  exec "$PY" -c "from sma_monitor.llm.codex_client import codex_available; from sma_monitor.llm import get_provider; p=get_provider(stage='decision'); print('codex_available:', codex_available(), '| provider:', type(p).__name__ if p else None, '| effort:', getattr(p,'effort',None))"
fi

# --- normal path: bootstrap schema (idempotent) then run the requested cycle -
case "$CMD" in
  collect|dispatch|thesis-email) ;;
  *) echo "usage: $0 <collect|dispatch|thesis-email|selfcheck>" >&2; exit 2 ;;
esac

cd "$REPO"
TS="$(date +%Y-%m-%d)"
{
  echo "===== $(date -Is) :: sma_cron $CMD ====="
  "$PY" -m sma_monitor                                # Phase 0 bootstrap (idempotent)
  "$PY" -m sma_monitor.orchestrator "$CMD"
} >> "$LOG_DIR/$CMD-$TS.log" 2>&1

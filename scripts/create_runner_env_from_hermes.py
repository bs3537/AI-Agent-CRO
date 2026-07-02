#!/usr/bin/env python3
"""Create /opt/data/sma-monitor/.env without printing secret values.

Pulls already-present VPS/Hermes environment values into the local AI-CRO
runner env. Missing values are left as blank assignments so the operator can
fill them in later. The file is mode 600 and .gitignored by the repo.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

APP = Path('/opt/data/sma-monitor')
ENV_PATH = APP / '.env'
SOURCE_ENV = Path('/opt/data/.env')


def parse_env_file(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    if not path.exists():
        return vals
    for raw in path.read_text(errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        m = re.match(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        vals[key] = val
    return vals

file_env = parse_env_file(SOURCE_ENV)

def get(*keys: str) -> str:
    for key in keys:
        val = os.environ.get(key)
        if val:
            return val
        val = file_env.get(key)
        if val:
            return val
    return ''

# Preserve any existing local app-specific values the user may have added.
existing = parse_env_file(ENV_PATH)

def choose(key: str, *source_keys: str, default: str = '') -> str:
    if existing.get(key):
        return existing[key]
    return get(*(source_keys or (key,))) or default

vals = {
    # Runtime role: Hermes/VPS runner only, never dashboard.
    'SMA_DEPLOYMENT_ROLE': 'runner',
    'DATA_ROOT': '/opt/data/sma-monitor/data',
    'LOG_LEVEL': 'INFO',
    # Use the local Hermes-backed Codex shim; it resolves /opt/data/auth.json
    # through Hermes auth code and never copies/prints token material.
    'SMA_CODEX_BIN': '/opt/data/bin/sma-codex',
    'CODEX_HOME': '/opt/data',
    'HERMES_HOME': '/opt/data',
    'SMA_CODEX_MODEL': choose('SMA_CODEX_MODEL', default='gpt-5.5'),
    'SMA_LLM_CONCURRENCY': choose('SMA_LLM_CONCURRENCY', default='4'),
    'SMA_LLM_MAX_RETRIES': choose('SMA_LLM_MAX_RETRIES', default='4'),
    'SMA_LLM_BACKOFF_BASE_S': choose('SMA_LLM_BACKOFF_BASE_S', default='2.0'),
    # External services. Blank values mean not available in this Hermes env yet.
    # Generic current-web research uses Codex GPT-5.5 native web search; do not
    # copy a Brave Search API key into the runner env.
    'TURSO_DATABASE_URL': choose('TURSO_DATABASE_URL'),
    'TURSO_AUTH_TOKEN': choose('TURSO_AUTH_TOKEN'),
    'RESEND_API_KEY': choose('RESEND_API_KEY'),
    'RESEND_EMAIL_FROM': choose('RESEND_EMAIL_FROM'),
    'RESEND_EMAIL_TO': choose('RESEND_EMAIL_TO'),
    'IBKR_FLEX_TOKEN': choose('IBKR_FLEX_TOKEN'),
    'IBKR_FLEX_QUERY_ID': choose('IBKR_FLEX_QUERY_ID'),
    'SEMANTIC_SCHOLAR_API_KEY': choose('SEMANTIC_SCHOLAR_API_KEY'),
    'FMP_API_KEY': choose('FMP_API_KEY'),
    'SEC_EDGAR_USER_AGENT': choose('SEC_EDGAR_USER_AGENT', default='AI CRO monitor'),
}

lines = [
    '# AI CRO / SMA Monitor Hermes runner env',
    '# Created locally on VPS; mode 600; do not commit or paste contents.',
]
for key, val in vals.items():
    safe = val.replace('\\', '\\\\').replace('\n', '\\n')
    # Quote values to preserve spaces/angle brackets in sender/user-agent fields.
    safe = safe.replace('"', '\\"')
    lines.append(f'{key}="{safe}"')
lines.append('')
ENV_PATH.write_text('\n'.join(lines))
os.chmod(ENV_PATH, 0o600)
print('wrote_env:', str(ENV_PATH))
print('mode:', oct(ENV_PATH.stat().st_mode & 0o777))
for key in vals:
    print(f'{key}: {"set" if vals[key] else "missing"}')

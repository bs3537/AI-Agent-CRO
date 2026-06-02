---
name: Deployment build fix for uv + Nix
description: How to get the Replit cloud_run build pipeline to install Python deps without hitting the read-only Nix store
---

Root cause: Replit's deployment build runs `uv sync` first. With real deps in pyproject.toml, uv tries to install into the Nix store (read-only) → build fails.

**Fix:**
1. `pyproject.toml`: set `dependencies = []` and `[tool.uv] package = false` — makes `uv sync` a no-op (nothing to install).
2. `requirements.txt`: list all real runtime deps here.
3. Build command: `python -m venv .venv && .venv/bin/pip install --no-user -r requirements.txt -e . && cd frontend && npm install && npm run build`
4. Run command: `.venv/bin/python -m sma_monitor.api --host 0.0.0.0 --port 5000`

**Why `--no-user`:** Nix-level `pip.conf` has `user = yes` globally. `--no-user` overrides it so pip installs into `.venv` (writable) not the user site (also problematic in build containers).

**Dev workflow:** Uses same `.venv/` approach. Command: `(test -f .venv/bin/pip || python -m venv .venv) && (.venv/bin/python -c 'import fastapi' 2>/dev/null || .venv/bin/pip install --no-user -q -r requirements.txt -e .) && .venv/bin/python -m sma_monitor.api --host localhost --port 8000`. No `waitForPort`.

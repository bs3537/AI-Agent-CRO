# SMA Monitor — dashboard (Workstream 6)

React + TypeScript + MUI SPA (Vite). Dark theme, neon-orange accent
(`#FF6A00`, change in `src/theme.ts`). Talks to the FastAPI backend (W5).

## Dev

```bash
# 1. start the API (from repo root)
python -m sma_monitor.api            # http://127.0.0.1:8000

# 2. start the dashboard
cd frontend
npm install
npm run dev                          # http://localhost:5173
```

The Vite dev server proxies `/api` → `http://127.0.0.1:8000` (see
`vite.config.ts`), so no CORS config is needed in dev.

## Production build

```bash
cd frontend
npm run build                        # emits frontend/dist/
```

The backend serves `frontend/dist` as static files automatically when it
exists (see `api/app.py`), so in production you run only the API process and
hit it directly. Override the bundle location with `SMA_FRONTEND_DIST`.

## What it shows

- Positions grid: ticker, open P&L (green ▲ / red ▼) + %NAV, conviction tier,
  stage, nearest catalyst.
- Colored HOLD / WATCH / SELL chip + the decision note and driver chips.
- Inline thesis editor (debounced autosave → `PUT /thesis`).
- Upload thesis docs (`.txt/.md/.pdf/.docx` → `POST /files`).
- Per-row **Recompute** (synchronous `POST /recompute?wait=true`).
- Detail drawer: scored articles, red-team bear cases, files (deletable),
  catalysts.
- Grid + status poll every 30s so decisions refresh after batch runs.

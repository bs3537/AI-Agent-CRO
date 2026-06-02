---
name: Frontend MUI missing .d.ts build failures
description: Why the production frontend build (tsc -b) fails on @mui/icons-material with TS7016 while dev works fine
---

Symptom: deployment build fails at `tsc -b && vite build` with `TS7016: Could not find a declaration file for module '@mui/icons-material/Refresh'` (and Search, Send, etc.). Dev mode works fine because vite dev does not run tsc type-checking.

Root cause: the dev `node_modules/@mui/icons-material` was a partial/interrupted install — many icons had `.js` but were missing their sibling `.d.ts`. The deployment copies the repl filesystem (including node_modules), so the broken install propagated to the build.

**Fix:** clean reinstall — `cd frontend && rm -rf node_modules package-lock.json && npm install`, then verify with `npm run build`. Do NOT bump MUI to v7 (the components are written for v5; a major bump risks breaking them). Keep `@mui/material` and `@mui/icons-material` on the same major.

**How to verify:** `npm run build` must pass locally — that is the same command the deployment runs, so a local pass means the deploy build passes.

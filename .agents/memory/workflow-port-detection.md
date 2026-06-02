---
name: Workflow waitForPort pitfall
description: Why compound shell commands fail Replit's port detection and how to fix it
---

When a workflow command is a compound shell chain (e.g. `cmd1 && cmd2 && server`), bash stays alive as the root process. Replit's port detection tracks the ROOT process — bash — which never opens the server port. The server child process does open it, but Replit doesn't see it.

**Fix:** Omit `waitForPort` from `configureWorkflow`. The workflow is considered "running" as long as the process is alive. The server is still fully accessible on its port.

**Why:** `exec server` as the last step would replace bash with the server, but only works if no subshells `()` are in the command. Without `exec`, just drop `waitForPort`.

**How to apply:** Any Backend API or similar workflow that has prefix commands (venv setup, health checks) before the server should NOT use `waitForPort`.

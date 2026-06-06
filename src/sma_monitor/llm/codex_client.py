"""OpenAI Codex CLI backend (ChatGPT-subscription login).

Drives the Codex CLI non-interactively via `codex exec`, authenticated by the
host's `codex login` (credentials in ~/.codex/auth.json) — no API key. We use
two shapes:

  - structured:  codex exec --output-schema <schema> -o <out> ...   (JSON object)
  - free text:   codex exec ...                                     (final message)

The prompt (system instructions + user content) is piped on stdin (`-`). The
sandbox stays read-only: these are pure question/answer calls, not agentic
edits. If anything goes wrong we raise LLMError so the caller can fall back to
its heuristic path.

Env overrides:
  SMA_CODEX_BIN    path to the codex binary (default: "codex"; lets tests stub it)
  SMA_CODEX_MODEL  model slug passed via -m (optional; default = account default)
  CODEX_HOME       auth/config dir (default: ~/.codex), per the Codex CLI

W9 throughput knobs (per-call model/effort come from llm/throughput.py; rate-
limit retry is local to _run):
  SMA_LLM_MAX_RETRIES     bounded retries on a rate-limited (429) exec (default 4)
  SMA_LLM_BACKOFF_BASE_S  base seconds for exponential backoff (default 2.0)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .provider import LLMError

log = logging.getLogger("sma_monitor.llm.codex_client")

# Label written to model_used columns and the cost ledger. The concrete model
# (gpt-5.x) is whatever the logged-in account/config selects; we don't pin it.
# Per-stage model/effort tiering (W9) is applied at the CLI layer only, so this
# label — and therefore cost-ledger pricing — stays stable across stages.
MODEL_LABEL = "codex-cli"

# Wall-clock ceiling for a single exec call. A stuck call should fail fast and
# let the caller dead-letter or fall back rather than hang a batch.
CALL_TIMEOUT_S = 180

# Defaults for the W9 rate-limit retry; both overridable via env (read at call
# time). The backoff is capped so a long batch can't stall indefinitely.
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0

# Substrings (case-insensitive) that mark a failed exec as rate-limited and
# therefore worth retrying with backoff, rather than a hard failure.
_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
    "usage limit",
    "quota",
)

_CODEX_AVAILABLE_CACHE: dict[tuple[str, str], bool] = {}


# Path to the codex binary, honoring the SMA_CODEX_BIN override used by tests.
def _codex_bin() -> str:
    return os.environ.get("SMA_CODEX_BIN", "codex")


# Directory holding the Codex login credentials (auth.json).
def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


# True when the Codex CLI is installed AND a login exists — the gate
# get_provider() uses to decide between Codex and the heuristic fallback.
def codex_available() -> bool:
    binary = _codex_bin()
    resolved = shutil.which(binary)
    if resolved is None:
        return False
    if not os.environ.get("SMA_CODEX_BIN") and not (_codex_home() / "auth.json").exists():
        return False

    # `SMA_CODEX_BIN` can point at a shim. Verify it can actually start under
    # the current PATH/interpreter before selecting the live Codex provider.
    cache_key = (resolved, os.environ.get("PATH", ""))
    cached = _CODEX_AVAILABLE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("codex_availability_check_failed", extra={"error": str(e)[:200]})
        _CODEX_AVAILABLE_CACHE[cache_key] = False
        return False
    available = proc.returncode == 0
    if not available:
        log.warning(
            "codex_availability_check_failed",
            extra={"stderr": (proc.stderr or "")[:300]},
        )
    _CODEX_AVAILABLE_CACHE[cache_key] = available
    return available


# Codex-backed provider. Stateless apart from the optional per-stage model +
# reasoning effort (W9 tiering); a fresh instance per stage is the norm.
# Thread-safe: every call uses its own temp dir + subprocess, so the per-item
# loops can run several instances concurrently.
class CodexProvider:
    model_label = MODEL_LABEL

    # model/effort are the W9 per-stage overrides (None = account default / no
    # effort flag). get_provider(stage=...) fills them from llm/throughput.py.
    def __init__(self, *, model: str | None = None, effort: str | None = None):
        self.model = model
        self.effort = effort

    # Run a structured completion. Writes `schema` to a temp file, asks Codex
    # to emit schema-conforming JSON to an output file, and parses it. Falls
    # back to scraping a JSON object from stdout when no schema is supplied.
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = 512,
    ) -> dict:
        prompt = _combine(system, user)
        with tempfile.TemporaryDirectory(prefix="sma_codex_") as td:
            args = ["exec", "--skip-git-repo-check", "--color", "never"]
            out_path: Path | None = None
            if schema is not None:
                schema_path = Path(td) / "schema.json"
                out_path = Path(td) / "out.json"
                schema_path.write_text(json.dumps(schema))
                args += ["--output-schema", str(schema_path), "-o", str(out_path)]
            args.append("-")  # read the full prompt from stdin
            stdout = _run(args, prompt, model=self.model, effort=self.effort)
            raw = out_path.read_text() if out_path and out_path.exists() else stdout
        return _extract_json_object(raw)

    # Run a free-text completion (used for the digest narrative). Returns the
    # final agent message with any markdown fence stripped.
    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 600,
    ) -> str:
        prompt = _combine(system, user)
        stdout = _run(
            ["exec", "--skip-git-repo-check", "--color", "never", "-"],
            prompt, model=self.model, effort=self.effort,
        )
        return _strip_fence(stdout).strip()


# Merge the system instructions and user content into one prompt. The Codex
# exec interface takes a single prompt, so we label the sections explicitly.
def _combine(system: str, user: str) -> str:
    return f"{system.strip()}\n\n---\n\n{user.strip()}\n"


# Invoke `codex` with the given args, feeding `prompt` on stdin. Returns
# stdout. Injects the W9 per-call model (-m) and reasoning effort
# (-c model_reasoning_effort=…) right after the `exec` subcommand, and retries
# with exponential backoff when the exec fails as rate-limited. Raises LLMError
# on non-rate-limit non-zero exit, timeout, or a missing binary.
def _run(args: list[str], prompt: str, *, model: str | None = None, effort: str | None = None) -> str:
    cmd = [_codex_bin(), *args]
    # Per-call override falls back to the legacy global SMA_CODEX_MODEL.
    model = model or os.environ.get("SMA_CODEX_MODEL")
    opts: list[str] = []
    if effort:
        opts += ["-c", f"model_reasoning_effort={effort}"]
    if model:
        opts += ["-m", model]
    if opts:
        cmd[2:2] = opts  # insert just after the `exec` subcommand

    max_retries = _max_retries()
    attempt = 0
    while True:
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=CALL_TIMEOUT_S,
            )
        except FileNotFoundError as e:
            raise LLMError(f"codex binary not found: {cmd[0]}") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"codex exec timed out after {CALL_TIMEOUT_S}s") from e
        if proc.returncode == 0:
            return proc.stdout or ""
        stderr = proc.stderr or ""
        # Rate-limited and retries remain → back off and try again.
        if _is_rate_limited(stderr) and attempt < max_retries:
            delay = _backoff_delay(attempt)
            log.warning(
                "codex_rate_limited_retry",
                extra={"attempt": attempt + 1, "max": max_retries, "sleep_s": delay},
            )
            time.sleep(delay)
            attempt += 1
            continue
        raise LLMError(f"codex exec failed (exit {proc.returncode}): {stderr[:400]}")


# True when a failed exec's stderr looks like a rate-limit / quota response
# (worth retrying) rather than a hard error.
def _is_rate_limited(stderr: str) -> bool:
    s = stderr.lower()
    return any(m in s for m in _RATE_LIMIT_MARKERS)


# Bounded retry count for rate-limited execs (env-overridable, read at call time).
def _max_retries() -> int:
    try:
        return max(0, int(os.environ.get("SMA_LLM_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))))
    except ValueError:
        return DEFAULT_MAX_RETRIES


# Exponential backoff delay for the Nth (0-based) retry: base * 2**n, capped.
def _backoff_delay(attempt: int) -> float:
    try:
        base = float(os.environ.get("SMA_LLM_BACKOFF_BASE_S", str(DEFAULT_BACKOFF_BASE_S)))
    except ValueError:
        base = DEFAULT_BACKOFF_BASE_S
    return min(BACKOFF_CAP_S, base * (2 ** attempt))


# Strip a leading/trailing ```json fence from a text block, if present.
def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t


# Parse the outermost JSON object from Codex output. Tolerates markdown
# fences, JSONL event streams, and stray preamble around the object.
def _extract_json_object(raw: str) -> dict:
    text = _strip_fence(raw)
    # Fast path: the whole payload is the object.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # Fallback: grab the last {...} block (handles JSONL/event noise).
    matches = re.findall(r"\{.*?\}", text, re.DOTALL)
    for chunk in reversed(matches):
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise LLMError(f"no JSON object in codex output: {raw[:300]}")

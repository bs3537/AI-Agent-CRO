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
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .provider import LLMError

# Label written to model_used columns and the cost ledger. The concrete model
# (gpt-5.x) is whatever the logged-in account/config selects; we don't pin it.
MODEL_LABEL = "codex-cli"

# Wall-clock ceiling for a single exec call. A stuck call should fail fast and
# let the caller dead-letter or fall back rather than hang a batch.
CALL_TIMEOUT_S = 180


# Path to the codex binary, honoring the SMA_CODEX_BIN override used by tests.
def _codex_bin() -> str:
    return os.environ.get("SMA_CODEX_BIN", "codex")


# Directory holding the Codex login credentials (auth.json).
def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


# True when the Codex CLI is installed AND a login exists — the gate
# get_provider() uses to decide between Codex and the heuristic fallback.
def codex_available() -> bool:
    if shutil.which(_codex_bin()) is None:
        return False
    # A stubbed binary (tests) signals readiness without a real auth.json.
    if os.environ.get("SMA_CODEX_BIN"):
        return True
    return (_codex_home() / "auth.json").exists()


# Codex-backed provider. One instance per call site is fine; it holds no state.
class CodexProvider:
    model_label = MODEL_LABEL

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
            stdout = _run(args, prompt)
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
        stdout = _run(["exec", "--skip-git-repo-check", "--color", "never", "-"], prompt)
        return _strip_fence(stdout).strip()


# Merge the system instructions and user content into one prompt. The Codex
# exec interface takes a single prompt, so we label the sections explicitly.
def _combine(system: str, user: str) -> str:
    return f"{system.strip()}\n\n---\n\n{user.strip()}\n"


# Invoke `codex` with the given args, feeding `prompt` on stdin. Returns
# stdout. Raises LLMError on non-zero exit, timeout, or a missing binary.
def _run(args: list[str], prompt: str) -> str:
    cmd = [_codex_bin(), *args]
    model = os.environ.get("SMA_CODEX_MODEL")
    if model:
        # Insert -m right after the `exec` subcommand.
        cmd[2:2] = ["-m", model]
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
    if proc.returncode != 0:
        raise LLMError(
            f"codex exec failed (exit {proc.returncode}): {(proc.stderr or '')[:400]}"
        )
    return proc.stdout or ""


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

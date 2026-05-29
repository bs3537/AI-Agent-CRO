"""Provider protocol + selection.

Every LLM-backed stage (scorer, red team, digest narrative, decision engine)
talks to an `LLMProvider` rather than importing a vendor SDK directly. This
keeps the model backend swappable and the offline fallback uniform: when no
provider is available, `get_provider()` returns None and the caller uses its
heuristic path.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


# Raised for any provider-side failure (process error, parse error, timeout).
# Callers catch this to dead-letter the unit of work and/or fall back.
class LLMError(RuntimeError):
    pass


# The contract every backend implements. `complete_json` returns a parsed
# object validated against `schema` (when the backend supports schemas);
# `complete_text` returns free text (used for the digest narrative).
@runtime_checkable
class LLMProvider(Protocol):
    # Short label persisted into model_used columns and the cost ledger.
    model_label: str

    # Run one completion that must return a JSON object. `schema` is an
    # optional JSON Schema the backend should constrain output to.
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = 512,
    ) -> dict: ...

    # Run one completion that returns free-form text.
    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 600,
    ) -> str: ...


# Return the active provider, or None when no backend is available (the
# signal for callers to use their heuristic fallback). `prefer_offline`
# forces None so `--offline` flags and tests bypass the model entirely.
# `stage` (W9) selects that stage's tiered model + reasoning effort; omit it
# to get the account default (no effort flag).
def get_provider(
    *, prefer_offline: bool = False, stage: str | None = None
) -> LLMProvider | None:
    if prefer_offline:
        return None
    # Codex is the only backend today; import lazily so a missing CLI never
    # breaks import of modules that merely *might* use an LLM.
    from .codex_client import CodexProvider, codex_available

    if not codex_available():
        return None
    if stage is None:
        return CodexProvider()
    from .throughput import stage_effort, stage_model

    return CodexProvider(model=stage_model(stage), effort=stage_effort(stage))

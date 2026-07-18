"""Provider protocol + selection.

Every LLM-backed stage (scorer, red team, digest narrative, decision engine)
talks to an `LLMProvider` rather than importing a vendor SDK directly. This
keeps the model backend swappable and the offline fallback uniform: when no
provider is available, `get_provider()` returns None and the caller uses its
heuristic path.
"""
from __future__ import annotations

import logging
import threading
from typing import Protocol, runtime_checkable

log = logging.getLogger("sma_monitor.llm.provider")


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
        timeout_s: int | None = None,
    ) -> dict: ...

    # Run one completion that returns free-form text.
    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 600,
        timeout_s: int | None = None,
    ) -> str: ...


class FallbackProvider:
    """Try providers in order; expose the provider that answered this thread."""

    def __init__(
        self,
        *,
        primary: LLMProvider,
        fallbacks: list[LLMProvider],
        stage: str | None = None,
        alert_on_primary_failure: bool = False,
    ):
        self.primary = primary
        self.fallbacks = fallbacks
        self.stage = stage
        self.alert_on_primary_failure = alert_on_primary_failure
        self._local = threading.local()
        self.model = getattr(primary, "model", None)
        self.effort = getattr(primary, "effort", None)

    @property
    def model_label(self) -> str:
        return getattr(self._local, "model_label", self.primary.model_label)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = 512,
        timeout_s: int | None = None,
    ) -> dict:
        kwargs = {
            "system": system,
            "user": user,
            "schema": schema,
            "max_tokens": max_tokens,
        }
        if timeout_s is not None:
            kwargs["timeout_s"] = timeout_s
        return self._complete("complete_json", **kwargs)

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 600,
        timeout_s: int | None = None,
    ) -> str:
        kwargs = {
            "system": system,
            "user": user,
            "max_tokens": max_tokens,
        }
        if timeout_s is not None:
            kwargs["timeout_s"] = timeout_s
        return self._complete("complete_text", **kwargs)

    def _complete(self, method: str, **kwargs):
        errors: list[str] = []
        providers = [self.primary, *self.fallbacks]
        for idx, provider in enumerate(providers):
            try:
                out = getattr(provider, method)(**kwargs)
                self._local.model_label = provider.model_label
                if idx > 0 and self.alert_on_primary_failure:
                    from .alerts import alert_codex_failure

                    alert_codex_failure(
                        stage=self.stage,
                        method=method,
                        error=errors[0],
                        fallback_label=provider.model_label,
                        fallback_succeeded=True,
                    )
                return out
            except LLMError as e:
                errors.append(f"{provider.model_label}: {e}")
                log.warning(
                    "llm_provider_failed",
                    extra={"stage": self.stage, "method": method, "model": provider.model_label},
                )
                continue
        if self.alert_on_primary_failure and errors:
            from .alerts import alert_codex_failure

            alert_codex_failure(
                stage=self.stage,
                method=method,
                error=errors[0],
                fallback_label=self.fallbacks[-1].model_label if self.fallbacks else None,
                fallback_succeeded=False,
            )
        raise LLMError("all LLM providers failed: " + " | ".join(errors))


# Return the active provider, or None when no backend is available (the
# signal for callers to use their heuristic fallback). `prefer_offline`
# forces None so `--offline` flags and tests bypass the model entirely.
# Codex CLI is the only live LLM backend. When Codex is unavailable, callers
# receive None and use their deterministic heuristic fallback.
def get_provider(
    *, prefer_offline: bool = False, stage: str | None = None
) -> LLMProvider | None:
    if prefer_offline:
        return None
    # Import backends lazily so missing CLIs/SDKs never break modules that only
    # might use an LLM.
    from .codex_client import CodexProvider, codex_available

    if not codex_available():
        return None
    if stage is None:
        return CodexProvider()

    from .throughput import stage_effort, stage_model, stage_web_search

    return CodexProvider(
        model=stage_model(stage),
        effort=stage_effort(stage),
        web_search=stage_web_search(stage),
    )

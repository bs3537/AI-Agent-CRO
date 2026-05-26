"""LLM provider layer.

A thin abstraction over whatever model backend is available. The default
backend is the OpenAI Codex CLI driven by a ChatGPT-subscription login
(`codex login`), invoked non-interactively via `codex exec`. When no backend
is available (no `codex` on PATH / not logged in), `get_provider()` returns
None and callers fall back to the deterministic heuristics that Phases 3-4
already ship — preserving the offline mode the rest of the system relies on.
"""
from .provider import LLMError, LLMProvider, get_provider

__all__ = ["LLMError", "LLMProvider", "get_provider"]

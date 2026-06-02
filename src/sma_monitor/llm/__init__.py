"""LLM provider layer.

A thin abstraction over whatever model backend is available. The default
backend is OpenRouter via API key. The local Codex CLI remains an emergency
fallback for hosts without OpenRouter configured. When no backend is available,
`get_provider()` returns None and callers fall back to the deterministic
heuristics that Phases 3-4 already ship — preserving the offline mode the rest
of the system relies on.
"""
from .provider import LLMError, LLMProvider, get_provider

__all__ = ["LLMError", "LLMProvider", "get_provider"]

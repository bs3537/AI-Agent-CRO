"""LLM provider layer.

A thin abstraction over whatever model backend is available. The default
backend is the local Codex CLI authenticated by the host's Codex/ChatGPT
subscription login. When Codex is unavailable, `get_provider()` returns None
and callers fall back to the deterministic heuristics that Phases 3-4 already
ship, preserving the offline mode the rest of the system relies on.
"""
from .provider import LLMError, LLMProvider, get_provider

__all__ = ["LLMError", "LLMProvider", "get_provider"]

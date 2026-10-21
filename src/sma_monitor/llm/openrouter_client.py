"""OpenRouter backend (OpenAI-compatible chat completions over HTTPS).

Used by the dashboard AI chat. Authenticated by the OPENROUTER_API_KEY secret.

Env overrides:
  OPENROUTER_API_KEY  required to enable this backend
  OPENROUTER_MODEL    model slug (default: deepseek/deepseek-v4.1-flash-1017)
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .provider import LLMError

# Default model slug requested by the PM; overridable via OPENROUTER_MODEL.
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"

# OpenRouter chat-completions endpoint.
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Wall-clock ceiling for one completion call.
CALL_TIMEOUT_S = 120


# True when an OpenRouter API key is configured.
def openrouter_available() -> bool:
    return bool((os.environ.get("OPENROUTER_API_KEY") or "").strip())


# Resolve the model slug from env, falling back to the default.
def openrouter_model() -> str:
    return (os.environ.get("OPENROUTER_MODEL") or "").strip() or DEFAULT_MODEL


# LLMProvider implementation that calls OpenRouter.
class OpenRouterProvider:
    def __init__(self, *, model: str | None = None):
        self.model = model or openrouter_model()
        self.model_label = self.model

    # Send one chat completion and return the assistant text.
    def _chat(self, *, system: str, user: str, max_tokens: int, timeout_s: int | None,
              json_mode: bool = False) -> str:
        key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = httpx.post(
                API_URL,
                json=body,
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout_s or CALL_TIMEOUT_S,
            )
        except httpx.HTTPError as e:
            raise LLMError(f"OpenRouter request failed: {e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            text = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"OpenRouter response unreadable: {resp.text[:300]}") from e
        if not text or not text.strip():
            raise LLMError("OpenRouter returned an empty response")
        return text.strip()

    # Free-text completion.
    def complete_text(self, *, system: str, user: str, max_tokens: int = 600,
                      timeout_s: int | None = None) -> str:
        return self._chat(system=system, user=user, max_tokens=max_tokens, timeout_s=timeout_s)

    # JSON-object completion; schema is appended to the prompt as guidance.
    def complete_json(self, *, system: str, user: str, schema: dict | None = None,
                      max_tokens: int = 512, timeout_s: int | None = None) -> dict:
        if schema is not None:
            system = f"{system}\n\nReturn only a JSON object matching this schema:\n{json.dumps(schema)}"
        text = self._chat(system=system, user=user, max_tokens=max_tokens,
                          timeout_s=timeout_s, json_mode=True)
        match = re.search(r"\{.*\}", text, flags=re.S)
        try:
            return json.loads(match.group(0) if match else text)
        except json.JSONDecodeError as e:
            raise LLMError(f"OpenRouter returned invalid JSON: {text[:200]}") from e

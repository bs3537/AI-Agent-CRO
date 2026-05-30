"""OpenRouter LLM backend.

OpenRouter is the paid API fallback behind the Codex CLI subscription path.
It implements the same provider contract as Codex so scoring, red-team,
decision, digest, and chat callers keep using `get_provider()`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Sequence
from typing import Any

import httpx

from ..config import settings
from .provider import LLMError

log = logging.getLogger("sma_monitor.llm.openrouter_client")

DEFAULT_MODEL = "xiaomi/mimo-v2.5-pro"
DEFAULT_FALLBACK_MODEL = "minimax/minimax-m2.7"
DEFAULT_FILE_MODEL = "google/gemini-3.1-flash-lite"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_S = 180.0
BACKOFF_CAP_S = 60.0


def openrouter_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or settings.openrouter_api_key


def openrouter_available() -> bool:
    return bool(openrouter_api_key())


def primary_model() -> str:
    return os.environ.get("OPENROUTER_MODEL") or settings.openrouter_model or DEFAULT_MODEL


def fallback_models() -> list[str]:
    raw = os.environ.get("OPENROUTER_FALLBACK_MODELS") or settings.openrouter_fallback_models
    models = [m.strip() for m in (raw or "").split(",") if m.strip()]
    return models or [DEFAULT_FALLBACK_MODEL]


def file_model() -> str:
    return os.environ.get("OPENROUTER_FILE_MODEL") or settings.openrouter_file_model or DEFAULT_FILE_MODEL


class OpenRouterProvider:
    """OpenAI-compatible chat-completions provider through OpenRouter."""

    def __init__(self, *, model: str | None = None, api_key: str | None = None):
        self.model = model or primary_model()
        self._api_key = api_key

    @property
    def model_label(self) -> str:
        return f"openrouter:{self.model}"

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        max_tokens: int = 512,
    ) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(schema),
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] = (
                f"{system.strip()}\n\nReturn a single valid JSON object only."
            )
        try:
            data = _post_chat(body, api_key=self._api_key)
        except LLMError:
            if schema is None:
                raise
            # Some OpenRouter models do not support strict JSON Schema mode.
            # Fall back to plain JSON mode while still giving the schema text.
            body.pop("response_format", None)
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] = (
                f"{system.strip()}\n\nReturn a single JSON object matching this schema:\n"
                f"{json.dumps(schema)[:6000]}"
            )
            data = _post_chat(body, api_key=self._api_key)
        return _extract_json_object(_message_text(data))

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 600,
    ) -> str:
        data = _post_chat(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "max_tokens": max_tokens,
            },
            api_key=self._api_key,
        )
        return _message_text(data).strip()


def complete_multimodal_text(
    *,
    messages: Sequence[dict[str, Any]],
    model: str | None = None,
    max_tokens: int = 1200,
    plugins: list[dict[str, Any]] | None = None,
) -> str:
    """Call OpenRouter with multimodal message parts for upload parsing."""
    body: dict[str, Any] = {
        "model": model or file_model(),
        "messages": list(messages),
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    if plugins:
        body["plugins"] = plugins
    data = _post_chat(body)
    return _message_text(data).strip()


def _post_chat(body: dict[str, Any], *, api_key: str | None = None) -> dict[str, Any]:
    key = api_key or openrouter_api_key()
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set")

    base = os.environ.get("OPENROUTER_BASE_URL") or settings.openrouter_base_url or DEFAULT_BASE_URL
    url = f"{base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost/ai-cro-agent",
        "X-Title": "AI Chief Risk Officer",
    }
    max_retries = _max_retries()
    attempt = 0
    while True:
        try:
            with httpx.Client(timeout=_timeout_s()) as client:
                resp = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as e:
            if attempt < max_retries:
                time.sleep(_backoff_delay(attempt))
                attempt += 1
                continue
            raise LLMError(f"openrouter request failed: {e}") from e

        if resp.status_code < 400:
            try:
                return resp.json()
            except json.JSONDecodeError as e:
                raise LLMError(f"openrouter returned invalid JSON: {resp.text[:300]}") from e

        if resp.status_code in {408, 409, 425, 429, 500, 502, 503, 504} and attempt < max_retries:
            log.warning(
                "openrouter_retry",
                extra={"status_code": resp.status_code, "attempt": attempt + 1, "model": body.get("model")},
            )
            time.sleep(_backoff_delay(attempt))
            attempt += 1
            continue
        raise LLMError(f"openrouter failed ({resp.status_code}): {resp.text[:400]}")


def _message_text(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"openrouter response missing message content: {str(data)[:300]}") from e
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return str(content)


def _extract_json_object(raw: str) -> dict:
    text = _strip_fence(raw)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    matches = re.findall(r"\{.*?\}", text, re.DOTALL)
    for chunk in reversed(matches):
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise LLMError(f"no JSON object in openrouter output: {raw[:300]}")


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t


def _schema_name(schema: dict) -> str:
    raw = str(schema.get("title") or schema.get("$id") or "sma_response")
    name = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_")
    return name[:64] or "sma_response"


def _max_retries() -> int:
    try:
        return max(0, int(os.environ.get("SMA_LLM_MAX_RETRIES", "4")))
    except ValueError:
        return 4


def _backoff_delay(attempt: int) -> float:
    try:
        base = float(os.environ.get("SMA_LLM_BACKOFF_BASE_S", "2.0"))
    except ValueError:
        base = 2.0
    return min(BACKOFF_CAP_S, base * (2 ** attempt))


def _timeout_s() -> float:
    try:
        return max(5.0, float(os.environ.get("OPENROUTER_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))))
    except ValueError:
        return DEFAULT_TIMEOUT_S

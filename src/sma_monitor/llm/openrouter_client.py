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
from .web_search import TOOL_SPEC, run_web_search, web_search_available

# Max web_search tool rounds per answer.
MAX_TOOL_ROUNDS = 6

# Sent when searching must stop and the model has to give its final answer.
FINAL_NUDGE = (
    "Stop searching now. Using what you have found so far, give your final answer in the "
    "requested format. Do not call any tool."
)

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
    def __init__(self, *, model: str | None = None, web_search: bool = False):
        self.web_search = web_search and web_search_available()
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

    # Free-text completion. With web_search=True the model may call a web_search
    # tool (Perplexity) up to MAX_TOOL_ROUNDS times before answering.
    def complete_text(self, *, system: str, user: str, max_tokens: int = 600,
                      timeout_s: int | None = None) -> str:
        if self.web_search:
            return self._chat_with_tools(system=system, user=user, max_tokens=max_tokens,
                                         timeout_s=timeout_s)
        return self._chat(system=system, user=user, max_tokens=max_tokens, timeout_s=timeout_s)

    # POST a raw chat-completions body and return the first choice message.
    def _post(self, body: dict, timeout_s: int | None) -> dict:
        key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        try:
            resp = httpx.post(API_URL, json=body, headers={"Authorization": f"Bearer {key}"},
                              timeout=timeout_s or CALL_TIMEOUT_S)
        except httpx.HTTPError as e:
            raise LLMError(f"OpenRouter request failed: {e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"OpenRouter response unreadable: {resp.text[:300]}") from e

    # Tool-calling loop: execute requested searches, feed results back, return final text.
    def _chat_with_tools(self, *, system: str, user: str, max_tokens: int,
                         timeout_s: int | None) -> str:
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        force_final = False
        for round_no in range(MAX_TOOL_ROUNDS + 2):
            body: dict = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
            if round_no >= MAX_TOOL_ROUNDS and not force_final:
                force_final = True
                messages.append({"role": "user", "content": FINAL_NUDGE})
            if not force_final:
                body["tools"] = [TOOL_SPEC]
            msg = self._post(body, timeout_s)
            calls = msg.get("tool_calls") or []
            if not calls:
                text = (msg.get("content") or "").strip()
                if not text:
                    raise LLMError("OpenRouter returned an empty response")
                # The model sometimes prints a raw tool call as text; make it answer instead.
                if "DSML" in text and not force_final:
                    force_final = True
                    messages.append({"role": "user", "content": FINAL_NUDGE})
                    continue
                return text
            messages.append(msg)
            for call in calls:
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = run_web_search(str(args.get("query", "")))
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        raise LLMError("web search tool loop did not finish")

    # JSON-object completion; schema is appended to the prompt as guidance.
    def complete_json(self, *, system: str, user: str, schema: dict | None = None,
                      max_tokens: int = 512, timeout_s: int | None = None) -> dict:
        if schema is not None:
            system = f"{system}\n\nReturn only a JSON object matching this schema:\n{json.dumps(schema)}"
        if self.web_search:
            text = self._chat_with_tools(
                system=system + "\n\nReturn only the JSON object, no prose or code fences.",
                user=user, max_tokens=max_tokens, timeout_s=timeout_s,
            )
        else:
            text = self._chat(system=system, user=user, max_tokens=max_tokens,
                              timeout_s=timeout_s, json_mode=True)
        match = re.search(r"\{.*\}", text, flags=re.S)
        try:
            return json.loads(match.group(0) if match else text)
        except json.JSONDecodeError as e:
            raise LLMError(f"OpenRouter returned invalid JSON: {text[:200]}") from e

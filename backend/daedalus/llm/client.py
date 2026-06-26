"""Thin OpenAI-compatible chat-completions client.

Works against any server that speaks the `/v1/chat/completions` shape:
vLLM, NVIDIA NIM, llama.cpp `--server`, Ollama (`/v1`), LM Studio, the real
OpenAI API, Anthropic via a translating proxy, etc.

Two configuration surfaces:
  - LLM_BASE_URL / LLM_API_KEY / LLM_MODEL  → used by planning & default Argus
  - LLM_VERIFIER_MODEL                      → optional override for Argus

Designed for JSON-mode-ish output (we ask the model for a JSON blob, then
parse-with-retry to recover from prefix/suffix prose).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import structlog

from daedalus.core.settings import get_settings

log = structlog.get_logger()


class LLMError(RuntimeError):
    """Raised when the LLM call or its response can't be used."""


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
_BARE_OBJECT_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        verifier_model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.verifier_model = verifier_model or model
        self.timeout = timeout

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        response_format_json: bool = False,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Servers that honour OpenAI's `response_format` will return JSON;
        # ones that don't will just ignore the field, and we'll fall back to
        # the regex extractor.
        if response_format_json:
            body["response_format"] = {"type": "json_object"}

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=body, headers=headers)
            except httpx.RequestError as exc:
                raise LLMError(f"LLM request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"unexpected LLM response shape: {resp.text[:500]}") from exc

    async def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        max_retries: int = 1,
    ) -> Any:
        """Ask for a JSON object/array back, with one parse-retry on failure."""
        attempt = 0
        last_text = ""
        while attempt <= max_retries:
            try:
                text = await self.chat(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format_json=True,
                )
            except LLMError:
                if attempt == max_retries:
                    raise
                attempt += 1
                continue

            last_text = text
            extracted = extract_json(text)
            if extracted is not None:
                return extracted

            # Reprompt the model to emit valid JSON.
            messages = [
                *messages,
                ChatMessage(role="assistant", content=text),
                ChatMessage(
                    role="user",
                    content=(
                        "Your previous response was not valid JSON. "
                        "Reply ONLY with a single JSON object (no prose, no code fences)."
                    ),
                ),
            ]
            attempt += 1

        raise LLMError(f"LLM did not return parseable JSON; last text: {last_text[:500]}")


def extract_json(text: str) -> Any | None:
    """Try to coax JSON out of model output. Returns None if all attempts fail."""
    candidates: list[str] = []

    text_stripped = text.strip()
    if text_stripped.startswith(("{", "[")):
        candidates.append(text_stripped)

    for match in _JSON_BLOCK_RE.finditer(text):
        candidates.append(match.group(1))

    bare = _BARE_OBJECT_RE.search(text)
    if bare:
        candidates.append(bare.group(1))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


@lru_cache
def get_llm_client():
    """Return the configured LLM client.

    Branches on ``LLM_BACKEND``:
      * ``cli`` (default) → ``CliLLMClient`` shelling out to ``claude --print``
        and authenticating via the operator's Pro/Max OAuth state.
      * ``http`` → ``LLMClient`` against ``LLM_BASE_URL``.

    Both classes expose the same ``chat`` / ``chat_json`` API.
    """
    settings = get_settings()
    if settings.llm_backend == "cli":
        # Imported lazily so the HTTP backend doesn't pay the cost of
        # importing the CLI module if it's not used.
        from daedalus.llm.cli_backend import CliLLMClient

        return CliLLMClient(
            model=settings.llm_model,
            verifier_model=settings.llm_verifier_model,
            timeout=settings.llm_timeout_seconds,
        )
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        verifier_model=settings.llm_verifier_model,
        timeout=settings.llm_timeout_seconds,
    )

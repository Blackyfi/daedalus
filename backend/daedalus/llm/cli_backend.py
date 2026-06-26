"""Claude-CLI-backed LLM client.

Used when ``LLM_BACKEND=cli`` (the default). Instead of speaking the
OpenAI ``/v1/chat/completions`` shape over HTTP, this backend shells
out to the local ``claude`` CLI and lets it handle authentication
via the operator's Pro/Max subscription OAuth state (mounted into the
container at ``/root/.claude``).

The class exposes the same public surface as
``daedalus.llm.client.LLMClient`` so the rest of the platform doesn't
care which backend is wired in:

  * ``chat(messages, …) -> str``
  * ``chat_json(messages, …) -> Any``

Notes:

* ``ANTHROPIC_API_KEY`` is stripped from the subprocess env. If it's
  set, the CLI uses API-key auth and our subscription path silently
  goes unused — a real footgun.
* All built-in tools are disabled (``--tools ""``) and slash commands
  are off. Planning / Argus only need text completion; we don't want
  the CLI deciding to run shell commands or read files on its own.
* We run from ``/tmp`` to avoid CLAUDE.md auto-discovery polluting the
  prompt with the project's own memory.
* Conversation context is encoded into the user prompt with role
  markers — claude --print is single-turn, so multi-message reprompts
  (e.g. the JSON-retry loop) get serialised into one prompt.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog

# Reuse the HTTP backend's primitives so callers can keep importing
# `ChatMessage` / `LLMError` from `daedalus.llm.client` regardless of
# which backend is wired in.
from daedalus.llm.client import ChatMessage, LLMError, extract_json

log = structlog.get_logger()

__all__ = ["ChatMessage", "CliLLMClient", "LLMError"]


class CliLLMClient:
    """Drop-in for LLMClient that uses ``claude --print`` instead of HTTP."""

    def __init__(
        self,
        *,
        model: str,
        verifier_model: str | None = None,
        timeout: float = 180.0,
        cwd: str = "/tmp",
        executable: str = "claude",
    ) -> None:
        self.model = model
        self.verifier_model = verifier_model or model
        self.timeout = timeout
        self.cwd = cwd
        self.executable = executable
        # Kept for symmetry with LLMClient — unused by the CLI path.
        self.base_url = "cli://claude"
        self.api_key = None

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,  # ignored — `claude --print` doesn't expose it
        max_tokens: int = 2048,    # ignored ditto
        response_format_json: bool = False,
    ) -> str:
        del temperature, max_tokens
        target_model = model or self.model

        system_prompt, user_prompt = _split_messages(messages, response_format_json)
        return await self._invoke(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            model=target_model,
        )

    async def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        max_retries: int = 1,
    ) -> Any:
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

        raise LLMError(f"claude did not return parseable JSON; last text: {last_text[:500]}")

    # ── internals ────────────────────────────────────────────────────────

    async def _invoke(self, *, user_prompt: str, system_prompt: str, model: str) -> str:
        args = [
            self.executable,
            "--print",
            "--output-format", "json",
            "--no-session-persistence",
            "--tools", "",
            "--disable-slash-commands",
            "--exclude-dynamic-system-prompt-sections",
            "--model", model,
        ]
        if system_prompt:
            args.extend(["--append-system-prompt", system_prompt])

        # Strip ANTHROPIC_API_KEY so the CLI uses the OAuth subscription
        # state from $HOME/.claude/.credentials.json instead of API-key
        # auth (which would bill against a separate API quota). HOME varies
        # by container — root in api/iris/hermes, /home/daedalus in talos/argus.
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
        }
        env.setdefault("HOME", os.path.expanduser("~"))
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(input=user_prompt.encode()),
                timeout=self.timeout,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise LLMError(f"claude timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            tail = (err_b or b"").decode("utf-8", errors="replace")[-500:]
            raise LLMError(f"claude exited {proc.returncode}: {tail}")

        try:
            envelope = json.loads(out_b.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            head = out_b.decode("utf-8", errors="replace")[:500]
            raise LLMError(f"claude returned non-JSON output: {head}") from exc

        if envelope.get("is_error"):
            raise LLMError(
                f"claude reported error: {envelope.get('result') or envelope!r}"
            )

        result = envelope.get("result")
        if not isinstance(result, str):
            raise LLMError(f"unexpected claude envelope shape: {envelope!r}")

        # Best-effort cost telemetry — Pro/Max users see $0 actual spend
        # but the CLI still reports notional cost for visibility.
        cost = envelope.get("total_cost_usd")
        if cost:
            log.info(
                "claude_cli.usage",
                model=model,
                duration_ms=envelope.get("duration_ms"),
                notional_cost_usd=cost,
            )
        return result


def _split_messages(
    messages: list[ChatMessage], response_format_json: bool
) -> tuple[str, str]:
    """Render a multi-message conversation into (system_prompt, user_prompt).

    ``claude --print`` is single-turn, so we concatenate any prior assistant
    + retry messages into the user prompt with simple role markers. The
    system prompt is everything tagged ``role == "system"`` joined with
    blank lines.
    """
    system_parts: list[str] = []
    rendered: list[str] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
            continue
        if m.role == "assistant":
            rendered.append(f"[assistant said previously]\n{m.content}")
            continue
        rendered.append(m.content)

    user_prompt = "\n\n".join(rendered) if rendered else ""
    if response_format_json:
        user_prompt = (
            (user_prompt + "\n\n" if user_prompt else "")
            + "Reply with a single JSON object. No prose before or after, no code fences."
        )
    return "\n\n".join(system_parts), user_prompt

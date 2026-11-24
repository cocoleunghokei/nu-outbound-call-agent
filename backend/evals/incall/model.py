"""The in-call model over the DeepSeek Chat Completions API, streamed so latency can be timed.

Streaming is what Vapi does on a live call: the voice starts on the first token, so the time
to the first spoken token is what the business hears as silence. ``DeepSeekChat`` records it,
along with when the stream ended (any tool call is complete then) and the ``usage`` block
(``stream_options.include_usage``), which also carries DeepSeek's prompt-cache hit and miss
counts.

Network errors, 429 and 5xx are retried twice with a short backoff, so one blip doesn't ruin a
run. Nothing here logs a request or response body, or the key.
"""

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from evals.incall.records import ModelReply, ToolCall

REQUEST_TIMEOUT_S = 60.0
MAX_TOKENS = 600
RETRIES = 2
RETRY_BACKOFF_S = 2.0


class ModelError(Exception):
    """The model call failed after retries, or the provider refused it."""


class ChatModel(Protocol):
    """One model turn. Tests use a scripted fake; the live eval uses ``DeepSeekChat``."""

    name: str
    temperature: float | None

    async def reply(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        """Send the conversation so far and return the model's next reply."""
        ...


class DeepSeekChat:
    """``ChatModel`` on DeepSeek's OpenAI-compatible streaming endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int = MAX_TOKENS,
        thinking: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # "enabled" or "disabled" (DeepSeek's `thinking.type`); None leaves the provider default,
        # which is on: reasoning tokens count toward max_tokens and can use all of it.
        self.thinking = thinking
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._transport = transport

    def request_body(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        """The Chat Completions body. Temperature is left out when None (provider default)."""
        body: dict[str, Any] = {
            "model": self.name,
            "messages": list(messages),
            "tools": list(tools),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.thinking is not None:
            body["thinking"] = {"type": self.thinking}
        return body

    async def reply(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
    ) -> ModelReply:
        """One streamed call, retried on network errors, 429 and 5xx."""
        body = self.request_body(messages, tools)
        last = "no attempt"
        for attempt in range(RETRIES + 1):
            if attempt:
                await asyncio.sleep(RETRY_BACKOFF_S * attempt)
            try:
                return await self._stream(body)
            except _Retryable as exc:
                last = str(exc)
        raise ModelError(f"model call failed after {RETRIES + 1} attempts ({last})")

    async def _stream(self, body: dict[str, Any]) -> ModelReply:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=httpx.Timeout(REQUEST_TIMEOUT_S)
            ) as client:
                start = time.perf_counter()
                async with client.stream("POST", self._url, headers=headers, json=body) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise _Retryable(f"status {resp.status_code}")
                    if resp.status_code >= 400:
                        raise ModelError(f"status {resp.status_code}: check the key and model")
                    return await parse_stream(resp.aiter_lines(), start)
        except httpx.HTTPError as exc:
            raise _Retryable(type(exc).__name__) from None


class _Retryable(Exception):
    pass


async def parse_stream(lines: Any, start: float) -> ModelReply:
    """Assemble a streamed Chat Completions answer (``data: {...}`` lines) into a reply.

    Tool-call fragments arrive by ``index`` and are joined; ``first_token_s`` is the first
    non-empty ``delta.content``; ``served`` is the first chunk's ``model`` and
    ``system_fingerprint``. ``start`` is ``time.perf_counter()`` when the request left.
    """
    text: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    first_token: float | None = None
    usage: dict[str, int] = {}
    served = ""
    finish_reason = ""
    reasoned = False
    async for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            raise ModelError("stream chunk is not JSON") from None
        if not served and isinstance(chunk.get("model"), str):
            fingerprint = chunk.get("system_fingerprint")
            served = chunk["model"] + (f" {fingerprint}" if isinstance(fingerprint, str) else "")
        if isinstance(chunk.get("usage"), dict):
            usage = {k: v for k, v in chunk["usage"].items() if isinstance(v, int)}
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
            if delta.get("reasoning_content"):
                reasoned = True
            content = delta.get("content")
            if isinstance(content, str) and content:
                if first_token is None:
                    first_token = time.perf_counter() - start
                text.append(content)
            for frag in delta.get("tool_calls") or []:
                slot = calls.setdefault(
                    int(frag.get("index", 0)), {"id": "", "name": "", "args": ""}
                )
                slot["id"] = frag.get("id") or slot["id"]
                function = frag.get("function") or {}
                slot["name"] += function.get("name") or ""
                slot["args"] += function.get("arguments") or ""
    done = time.perf_counter() - start
    return ModelReply(
        text="".join(text).strip(),
        tool_calls=[_tool_call(i, slot) for i, slot in sorted(calls.items())],
        first_token_s=first_token,
        done_s=done,
        usage=usage,
        finish=(finish_reason or "none") + (", reasoning" if reasoned else ""),
        served=served,
    )


def _tool_call(index: int, slot: dict[str, str]) -> ToolCall:
    raw = slot["args"].strip() or "{}"
    try:
        parsed = json.loads(raw)
        args = parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        args = None
    return ToolCall(
        call_id=slot["id"] or f"call_{index}", name=slot["name"], args=args, raw_args=raw
    )

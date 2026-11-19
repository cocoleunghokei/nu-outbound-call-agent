"""Vapi adapter on httpx. Never retries, never logs URLs, never chains raw httpx errors.

A refusal's error message is logged, reduced to plain text (``_reject_detail``).
"""

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from app.config import Settings
from app.voice.fake import FakeVoicePlatform
from app.voice.port import (
    CreatedCall,
    VoiceNotSent,
    VoicePlatform,
    VoiceRejected,
    VoiceUncertain,
    prepare_assistant,
)
from app.voice.prompt_safety import Snapshot

# httpx logs every request URL at INFO, and control URLs are capabilities.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

DEFAULT_BASE_URL = "https://api.vapi.ai"
API_DEADLINE_S = 10.0
CONTROL_DEADLINE_S = 2.0
FIND_PAGE_LIMIT = 100
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# What a refusal may say in the log. Vapi's error body carries a ``message`` written by
# class-validator ("customer.number must be a string") or by Vapi itself ("Retired models
# cannot be saved ..."). The request those messages describe holds the destination number
# and the prompt built from the user's brief, so no word of a message is ever copied to the
# log: ``_reject_detail`` rebuilds it from the sentence templates below, with every variable
# part checked against a closed set or rendered as ``_``, and an unrecognised message
# becomes a fixed phrase naming only the validated property paths. Add a template when a
# real refusal needs one; never one with a free-text capture.
_PATH_SEGMENTS = frozenset(
    {
        # keys of the request the backend sends (assistant config, tools, create body)
        "assistant",
        "model",
        "provider",
        "messages",
        "role",
        "content",
        "tools",
        "type",
        "async",
        "function",
        "name",
        "description",
        "parameters",
        "properties",
        "required",
        "voice",
        "voiceId",
        "transcriber",
        "language",
        "server",
        "url",
        "headers",
        "serverMessages",
        "artifactPlan",
        "recordingEnabled",
        "transcriptPlan",
        "enabled",
        "monitorPlan",
        "listenEnabled",
        "controlEnabled",
        "firstMessage",
        "firstMessageMode",
        "maxDurationSeconds",
        "customer",
        "number",
        "phoneNumberId",
        "metadata",
        "call_id",
        "credential",
        "apiKey",
        # JSON Schema keywords Vapi has objected to in tool parameters
        "title",
        "$schema",
        "$id",
        "additionalProperties",
    }
)
_DEEPSEEK_MODELS = (
    "deepseek-chat",
    "deepseek-flash",
    "deepseek-reasoner",
    "deepseek-flash-thinking",
)
_MODEL_IDS = frozenset(_DEEPSEEK_MODELS) | frozenset(f"deep-seek/{m}" for m in _DEEPSEEK_MODELS)
_INDEX = re.compile(r"^\d{1,3}$")
_PUNCTUATION = ".,;:()\"'"
_BLANK = "_"
_DETAIL_MAX = 300
_UNRECOGNISED = "unrecognised refusal; paths: "


def _is_path(token: str) -> bool:
    """True for a property path of the request: known keys joined by ``.`` or ``[n]``."""
    parts = [p for p in re.split(r"[.\[\]]+", token) if p]
    if len(parts) < 2 or parts[0] not in _PATH_SEGMENTS:
        return False
    previous_was_index = False
    for part in parts[1:]:
        if part in _PATH_SEGMENTS:
            previous_was_index = False
        elif _INDEX.match(part) and not previous_was_index:
            previous_was_index = True
        else:
            return False
    return True


def _path_or_blank(token: str) -> str:
    return token if _is_path(token) else _BLANK


def _model_or_blank(token: str) -> str:
    return token if token in _MODEL_IDS else _BLANK


def _whitelist_prefix(prefix: str) -> str:
    """The nesting before ``property X should not exist``: single keys or paths, each with a dot."""
    pieces = [p for p in prefix.split("each value in ") if p]
    for piece in pieces:
        bare = piece[:-1] if piece.endswith(".") else piece
        if bare not in _PATH_SEGMENTS and not _is_path(bare):
            return f"{_BLANK} "
    return prefix


def _render_whitelist(m: re.Match[str]) -> str:
    key = m["key"] if m["key"] in _PATH_SEGMENTS else _BLANK
    return f"{_whitelist_prefix(m['prefix'])}property {key} should not exist"


def _render_retired(m: re.Match[str]) -> str:
    return (
        f"Retired models cannot be saved. {_model_or_blank(m['m'])} (model) was retired; "
        f"use {_model_or_blank(m['m2'])} instead."
    )


_EACH = r"^(?P<each>each value in )?(?P<p>\S+) "
_TEMPLATES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (
        re.compile(
            r"^(?P<prefix>(?:\S+\.)?(?:each value in \S+\.)*)"
            r"property (?P<key>\S+) should not exist$"
        ),
        _render_whitelist,
    ),
    (
        re.compile(_EACH + r"should not exist$"),
        lambda m: f"{m['each'] or ''}{_path_or_blank(m['p'])} should not exist",
    ),
    (
        re.compile(
            _EACH + r"must be (?P<article>an?) "
            r"(?P<kind>string|number|boolean|array|object|UUID|URL|valid phone number)$"
        ),
        lambda m: f"{m['each'] or ''}{_path_or_blank(m['p'])} must be {m['article']} {m['kind']}",
    ),
    (
        re.compile(_EACH + r"should not be empty$"),
        lambda m: f"{m['each'] or ''}{_path_or_blank(m['p'])} should not be empty",
    ),
    (
        re.compile(
            _EACH + r"must be (?P<cmp>longer|shorter) than or equal to (?P<n>\d{1,5}) characters$"
        ),
        lambda m: (
            f"{m['each'] or ''}{_path_or_blank(m['p'])} must be {m['cmp']} than or equal to "
            f"{m['n']} characters"
        ),
    ),
    (
        re.compile(_EACH + r"must be one of the following values: .*$"),
        lambda m: (
            f"{m['each'] or ''}{_path_or_blank(m['p'])} "
            f"must be one of the following values: {_BLANK}"
        ),
    ),
    (
        re.compile(
            r"^Retired models cannot be saved\. (?P<m>\S+) \(model\) was retired on "
            r"\d{4}-\d{2}-\d{2}; use (?P<m2>\S+) instead\.$"
        ),
        _render_retired,
    ),
]


def _render_item(item: str) -> str:
    """One message item as safe text: a template, or the paths it names and nothing else."""
    text = " ".join(item.split())
    for pattern, render in _TEMPLATES:
        match = pattern.match(text)
        if match:
            return render(match)
    paths: list[str] = []
    for token in text.split(" "):
        bare = token.strip(_PUNCTUATION)
        if _is_path(bare) and bare not in paths:
            paths.append(bare)
    return _UNRECOGNISED + (", ".join(paths) or "none")


def _reject_detail(response: httpx.Response) -> str | None:
    """Vapi's ``message`` for a refusal, rebuilt from recognised templates. See the note above."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    message = body.get("message")
    items = [message] if isinstance(message, str) else message if isinstance(message, list) else []
    rendered = [_render_item(item) for item in items if isinstance(item, str)]
    if not rendered:
        return None
    text = "; ".join(rendered)
    return text if len(text) <= _DETAIL_MAX else text[: _DETAIL_MAX - 1] + "…"


def _created_from(body: Any) -> CreatedCall:
    if not isinstance(body, dict) or not isinstance(body.get("id"), str):
        raise VoiceUncertain("voice platform returned an unreadable call body")
    monitor = body.get("monitor")
    monitor = monitor if isinstance(monitor, dict) else {}
    control = monitor.get("controlUrl")
    listen = monitor.get("listenUrl")
    ended_at = body.get("endedAt")
    reason = body.get("endedReason")
    return CreatedCall(
        body["id"],
        control if isinstance(control, str) else None,
        listen if isinstance(listen, str) else None,
        ended=body.get("status") == "ended" or (isinstance(ended_at, str) and bool(ended_at)),
        ended_reason=reason.strip() if isinstance(reason, str) else "",
    )


def _reject_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    for key in ("code", "error"):
        value = body.get(key)
        if isinstance(value, str) and _SAFE_CODE.match(value):
            return value
    return None


class VapiVoice:
    """VoicePlatform backed by the Vapi REST API."""

    def __init__(
        self,
        api_key: str,
        phone_number_id: str,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._phone_number_id = phone_number_id
        self._base = base_url.rstrip("/")
        self._transport = transport

    async def _request(
        self,
        method: str,
        url: str,
        deadline: float,
        *,
        authed: bool = True,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"} if authed else {}
        try:
            async with asyncio.timeout(deadline):
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=httpx.Timeout(deadline)
                ) as client:
                    response = await client.request(
                        method, url, headers=headers, json=json, params=params
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise VoiceNotSent("voice platform connection failed before sending") from None
        except (httpx.RequestError, httpx.InvalidURL, TimeoutError) as exc:
            raise VoiceUncertain(
                f"voice platform request outcome unknown ({type(exc).__name__})"
            ) from None
        if response.status_code >= 500:
            raise VoiceUncertain(f"voice platform error status {response.status_code}")
        if response.status_code >= 300:
            raise VoiceRejected(
                response.status_code, _reject_code(response), _reject_detail(response)
            )
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            raise VoiceUncertain("voice platform returned invalid JSON") from None

    async def create_call(
        self,
        assistant: dict[str, Any],
        customer_e164: str,
        metadata: dict[str, Any],
        snapshot: Snapshot,
    ) -> CreatedCall:
        config = prepare_assistant(assistant, snapshot)
        response = await self._request(
            "POST",
            f"{self._base}/call",
            API_DEADLINE_S,
            json={
                "assistant": config,
                "phoneNumberId": self._phone_number_id,
                "customer": {"number": customer_e164},
                "metadata": metadata,
            },
        )
        return _created_from(self._json(response))

    async def get_call(self, vapi_call_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"{self._base}/call/{vapi_call_id}", API_DEADLINE_S)
        body = self._json(response)
        if not isinstance(body, dict):
            raise VoiceUncertain("voice platform returned an unreadable call body")
        return body

    async def send_control(self, control_url: str, message: dict[str, Any]) -> None:
        await self._request("POST", control_url, CONTROL_DEADLINE_S, authed=False, json=message)

    async def find_call_by_app_id(
        self, call_id: UUID, created_after: datetime
    ) -> CreatedCall | None:
        # ASSUMPTION: GET /call accepts createdAtGt and limit; verify in the probe session.
        stamp = created_after.astimezone(UTC).isoformat(timespec="milliseconds")
        response = await self._request(
            "GET",
            f"{self._base}/call",
            API_DEADLINE_S,
            params={"createdAtGt": stamp.replace("+00:00", "Z"), "limit": FIND_PAGE_LIMIT},
        )
        body = self._json(response)
        items = body.get("results", []) if isinstance(body, dict) else body
        if not isinstance(items, list):
            raise VoiceUncertain("voice platform returned an unreadable call list")
        for item in items:
            metadata = item.get("metadata") if isinstance(item, dict) else None
            if isinstance(metadata, dict) and str(metadata.get("call_id")) == str(call_id):
                return _created_from(item)
        if len(items) >= FIND_PAGE_LIMIT:
            # The call may sit beyond the first page: "not found" would be a guess.
            raise VoiceUncertain("voice platform call list is full; creation not ruled out")
        return None


def get_voice(settings: Settings) -> VoicePlatform:
    """Pick the adapter named by ``settings.voice_platform``."""
    if settings.voice_platform == "vapi":
        return VapiVoice(
            settings.vapi_api_key.get_secret_value(),
            settings.vapi_phone_number_id,
            settings.vapi_base_url,
        )
    return FakeVoicePlatform()

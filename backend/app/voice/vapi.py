"""Vapi adapter on httpx. Never retries, never logs URLs, never chains raw httpx errors."""

import asyncio
import logging
import re
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


def _created_from(body: Any) -> CreatedCall:
    if not isinstance(body, dict) or not isinstance(body.get("id"), str):
        raise VoiceUncertain("voice platform returned an unreadable call body")
    monitor = body.get("monitor")
    monitor = monitor if isinstance(monitor, dict) else {}
    control = monitor.get("controlUrl")
    listen = monitor.get("listenUrl")
    return CreatedCall(
        body["id"],
        control if isinstance(control, str) else None,
        listen if isinstance(listen, str) else None,
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
            raise VoiceRejected(response.status_code, _reject_code(response))
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

"""Minimal Vapi REST client for placing and fetching calls."""
from __future__ import annotations

import json

from probe.control import ControlError, Transport


class VapiApi:
    """Create and fetch Vapi calls through an injected Transport."""

    def __init__(self, api_key: str, base_url: str, transport: Transport):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"}
        self._transport = transport

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        status, resp = self._transport(method, f"{self._base}{path}", dict(self._headers), body)
        text = resp.decode("utf-8", errors="replace")
        if not 200 <= status < 300:
            raise ControlError(f"Vapi {method} {path} returned {status}: {text}")
        return json.loads(text)

    def create_call(self, assistant: dict, phone_number_id: str, customer_number: str) -> dict:
        """Place an outbound call with an inline assistant config."""
        return self._request("POST", "/call", {
            "assistant": assistant, "phoneNumberId": phone_number_id,
            "customer": {"number": customer_number}})

    def get_call(self, call_id: str) -> dict:
        """Fetch a call by id."""
        return self._request("GET", f"/call/{call_id}")

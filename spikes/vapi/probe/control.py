"""Live call control: message builders and a retrying control-URL client."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from probe.config import validate_us_number

# (method, url, headers, body) -> (status, body). Raises OSError on network failure.
Transport = Callable[[str, str, dict[str, str], "bytes | None"], tuple[int, bytes]]


class ControlError(RuntimeError):
    """A Vapi API or control request failed."""


def urllib_transport(method: str, url: str, headers: dict[str, str],
                     body: bytes | None) -> tuple[int, bytes]:
    """Real Transport over urllib with a 10 s timeout; URLError/timeouts surface as OSError."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def say(content: str, end_call_after: bool = False) -> dict:
    """Make the assistant speak `content`, optionally hanging up afterwards."""
    return {"type": "say", "content": content, "endCallAfterSpoken": end_call_after}


def add_system_message(content: str, trigger_response: bool) -> dict:
    """Inject a system message into the conversation."""
    return {"type": "add-message", "message": {"role": "system", "content": content},
            "triggerResponseEnabled": trigger_response}


def mute_assistant() -> dict:
    """Mute the assistant."""
    return {"type": "control", "control": "mute-assistant"}


def unmute_assistant() -> dict:
    """Unmute the assistant."""
    return {"type": "control", "control": "unmute-assistant"}


def transfer_to(number: str) -> dict:
    """Transfer the call to a validated US number (raises ConfigError otherwise)."""
    validate_us_number(number)
    return {"type": "transfer", "destination": {"type": "number", "number": number}}


class ControlClient:
    """POSTs control messages to a live call's control URL."""

    def __init__(self, transport: Transport):
        self._transport = transport

    def send(self, control_url: str, message: dict, log: Callable[[str, dict], None]) -> bool:
        """Send `message`; try at most twice (OSError or 5xx), log once, never raise."""
        body = json.dumps(message).encode()
        headers = {"Content-Type": "application/json"}
        status: int | None = None
        error: str | None = None
        resp = b""
        for _ in range(2):
            try:
                status, resp = self._transport("POST", control_url, headers, body)
                error = None
            except Exception as e:  # send() must never raise, whatever the transport throws
                status, error = None, f"{type(e).__name__}: {e}"
                continue
            if 200 <= status < 300:
                log("control-sent", {"url": control_url, "message": message})
                return True
            if status < 500:
                break
        payload = {"url": control_url, "message": message, "status": status, "error": error}
        if status is not None:
            text = resp.decode("utf-8", "replace") if isinstance(resp, bytes) else str(resp)
            payload["body"] = text[:300]
        log("control-failed", payload)
        return False

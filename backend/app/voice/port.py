"""The voice platform port: the only seam between the backend and a telephony vendor."""

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.errors import ApiError
from app.voice.prompt_safety import Snapshot, assert_prompt_safe
from app.voice.tools import tool_definitions


@dataclass
class CreatedCall:
    """What the platform returned for a placed call. The URLs are capabilities: never log them."""

    vapi_call_id: str
    control_url: str | None
    listen_url: str | None


class UnsafeAssistantConfig(ApiError):
    """The assistant config breaks the recording/transcript rules. Raised before any request."""

    def __init__(self, message: str) -> None:
        super().__init__("unsafe_assistant_config", 500, message, False)


class VoiceNotSent(Exception):
    """The request provably never left (connection could not be made)."""


class VoiceUncertain(Exception):
    """The request may have been processed: timeout, dropped connection or 5xx."""


class VoiceRejected(Exception):
    """The platform answered with a 4xx."""

    def __init__(self, status: int, code: str | None = None) -> None:
        super().__init__(f"voice platform rejected the request: status {status}, code {code}")
        self.status = status
        self.code = code


class VoicePlatform(Protocol):
    """Operations the call controller needs from a voice platform."""

    async def create_call(
        self,
        assistant: dict[str, Any],
        customer_e164: str,
        metadata: dict[str, Any],
        snapshot: Snapshot,
    ) -> CreatedCall:
        """Place a call. Runs ``prepare_assistant`` before anything is sent."""
        ...

    async def get_call(self, vapi_call_id: str) -> dict[str, Any]:
        """Fetch a call by platform id."""
        ...

    async def send_control(self, control_url: str, message: dict[str, Any]) -> None:
        """Send a live-control message to a call's control URL."""
        ...

    async def find_call_by_app_id(
        self, call_id: UUID, created_after: datetime
    ) -> CreatedCall | None:
        """Find a call by our ``call_id`` after an uncertain creation (§4.1)."""
        ...


def _check_strings(node: Any, snapshot: Snapshot) -> None:
    if isinstance(node, str):
        assert_prompt_safe(node, snapshot)
    elif isinstance(node, dict):
        for value in node.values():
            _check_strings(value, snapshot)
    elif isinstance(node, list | tuple):
        for item in node:
            _check_strings(item, snapshot)


def prepare_assistant(assistant: dict[str, Any], snapshot: Snapshot) -> dict[str, Any]:
    """Return a safe copy of ``assistant`` or raise before anything is sent.

    Requires recording off and transcripts on, replaces ``model.tools`` with the fixed
    definitions, then checks every other string for ask-first values (AskFirstInPrompt).
    """
    config = copy.deepcopy(assistant)
    plan = config.get("artifactPlan")
    if not isinstance(plan, dict) or plan.get("recordingEnabled") is not False:
        raise UnsafeAssistantConfig("artifactPlan.recordingEnabled must be false")
    transcript = plan.get("transcriptPlan")
    if not isinstance(transcript, dict) or transcript.get("enabled") is not True:
        raise UnsafeAssistantConfig("artifactPlan.transcriptPlan.enabled must be true")
    model = config.get("model")
    if isinstance(model, dict):
        model.pop("tools", None)
    _check_strings(config, snapshot)
    if not isinstance(model, dict):
        model = config["model"] = {}
    model["tools"] = tool_definitions()
    return config

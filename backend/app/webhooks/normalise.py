"""Split a redacted Vapi body into units and decode each unit into our canonical form.

``split_units`` only lists raw entries (one per ``toolCallList`` entry, else one for the whole
message) and never fails on content. ``normalise_unit`` decodes and validates one unit and
raises ``NormaliseError`` for a malformed one; the ingress runs it inside that unit's own
savepoint, so a bad entry fails only itself.

Vapi shapes (Vapi docs and spikes/vapi/probe/webhook.py):

- ``message.type`` names the message; ``message.timestamp`` is epoch milliseconds.
- Transcripts: ``type == "transcript"`` (or ``transcript[transcriptType="final"]``),
  ``transcriptType`` ``partial``/``final``, ``role`` and ``transcript`` (the text).
- Tool calls: ``type == "tool-calls"`` with ``toolCallList[] = {id, type: "function",
  function: {name, arguments}}``; ``arguments`` is a JSON string or an object.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.contracts import validator_for
from app.webhooks.redact import redact

# Vapi role -> our transcript role. On an outbound call Vapi's "user"/"customer" is the far
# end, the business; "assistant"/"bot" is our agent. Our "user" (the app's user) is never a
# Vapi speaker.
ROLE_MAP = {"assistant": "agent", "bot": "agent", "user": "business", "customer": "business"}

# Score fields go to agent_scores only (DC-5); handlers never see them.
SCORE_FIELDS = ("confidence", "dimensions")

_TOOL_NAME = re.compile(r"^[a-z][a-z_]{0,63}$")


class NormaliseError(ValueError):
    """A unit of a Vapi body is malformed or does not match its contract."""


@dataclass(frozen=True)
class RawUnit:
    """One unit of work in a redacted body: a toolCallList entry, or the whole message."""

    message: dict[str, Any] = field(repr=False)
    entry: Any = field(repr=False)
    index: int

    @property
    def tool_call_id(self) -> str | None:
        """The entry's toolCallId, if it has a usable one."""
        if isinstance(self.entry, dict):
            value = self.entry.get("id")
            if isinstance(value, str) and value:
                return value
        return None


@dataclass(frozen=True)
class TranscriptUnit:
    """A final transcript, ready for ``append_event("transcript.final", ...)``."""

    payload: dict[str, Any]
    occurred_at: datetime
    dedup_key: str | None


@dataclass(frozen=True)
class ToolCallUnit:
    """One validated tool call."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any] = field(repr=False)
    source_ts: datetime | None


@dataclass(frozen=True)
class OtherUnit:
    """Any other message type: goes to the event registry (or stays raw-only)."""

    vapi_type: str


Normalised = TranscriptUnit | ToolCallUnit | OtherUnit


def message_type(message: dict[str, Any]) -> str:
    """Return ``message.type`` as a string ("unknown" when absent or not a string)."""
    value = message.get("type")
    return value if isinstance(value, str) and value else "unknown"


def is_transcript(message: dict[str, Any]) -> bool:
    """True for ``transcript`` and the ``transcript[transcriptType="..."]`` variants."""
    kind = message_type(message)
    return kind == "transcript" or kind.startswith("transcript[")


def is_partial_transcript(message: dict[str, Any]) -> bool:
    """True if the message is a partial (interim) transcript, which is never stored."""
    return _transcript_kind(message) == "partial"


def _transcript_kind(message: dict[str, Any]) -> str | None:
    """``partial``/``final`` from ``transcriptType`` or the type suffix; None if neither."""
    if not is_transcript(message):
        return None
    kind = message.get("transcriptType")
    if isinstance(kind, str):
        return kind
    for candidate in ("partial", "final"):
        if f'transcriptType="{candidate}"' in message_type(message):
            return candidate
    return None


def message_timestamp(message: dict[str, Any]) -> datetime | None:
    """Vapi's ``message.timestamp`` (epoch ms, or an ISO string) as UTC, or None."""
    value = message.get("timestamp")
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value / 1000, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    return None


def iso_ms(value: datetime) -> str:
    """ISO 8601 UTC with milliseconds, e.g. ``2026-11-07T20:00:05.123Z``."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def split_units(redacted_body: Any) -> list[RawUnit]:
    """List the units of a redacted body without decoding or validating them."""
    message = redacted_body.get("message") if isinstance(redacted_body, dict) else None
    if not isinstance(message, dict):
        return []
    entries = message.get("toolCallList")
    if message_type(message) == "tool-calls" and isinstance(entries, list) and entries:
        return [RawUnit(message=message, entry=entry, index=i) for i, entry in enumerate(entries)]
    return [RawUnit(message=message, entry=None, index=0)]


def _transcript(message: dict[str, Any]) -> TranscriptUnit:
    kind = _transcript_kind(message)
    if kind != "final":
        raise NormaliseError(f"only final transcripts are stored, got {kind!r}")
    vapi_role = message.get("role")
    role = ROLE_MAP.get(vapi_role) if isinstance(vapi_role, str) else None
    if role is None:
        raise NormaliseError(f"unknown transcript role {vapi_role!r}")
    text = message.get("transcript")
    if not isinstance(text, str):
        raise NormaliseError("transcript text is missing")
    start = message_timestamp(message)
    start_iso = iso_ms(start) if start is not None else None
    return TranscriptUnit(
        payload={"role": role, "text": text, "source_start_ts": start_iso},
        occurred_at=start or datetime.now(UTC),
        dedup_key=f"transcript:{role}:{start_iso}" if start_iso is not None else None,
    )


def _tool_call(unit: RawUnit, secrets: set[str]) -> ToolCallUnit:
    entry = unit.entry
    tool_call_id = unit.tool_call_id
    if tool_call_id is None or not isinstance(entry, dict):
        raise NormaliseError(f"tool call entry {unit.index} has no id")
    function = entry.get("function")
    if not isinstance(function, dict):
        raise NormaliseError(f"tool call {tool_call_id} has no function")
    name = function.get("name")
    if not isinstance(name, str) or not _TOOL_NAME.match(name):
        raise NormaliseError(f"tool call {tool_call_id} has an invalid tool name")
    raw_args = function.get("arguments", {})
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except ValueError as exc:
            raise NormaliseError(f"tool call {tool_call_id}: undecodable arguments") from exc
    if not isinstance(raw_args, dict):
        raise NormaliseError(f"tool call {tool_call_id}: arguments are not an object")
    arguments: dict[str, Any] = redact(raw_args, secrets)
    try:
        validator = validator_for(f"tools/{name}.args.v1.json")
    except FileNotFoundError as exc:
        raise NormaliseError(f"tool call {tool_call_id}: unknown tool {name!r}") from exc
    errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise NormaliseError(f"invalid {name} arguments for tool call {tool_call_id}: {details}")
    return ToolCallUnit(
        tool_call_id=tool_call_id,
        name=name,
        arguments=arguments,
        source_ts=message_timestamp(unit.message),
    )


def normalise_unit(unit: RawUnit, secrets: set[str]) -> Normalised:
    """Decode and validate one unit. Raises NormaliseError for a malformed unit.

    ``secrets`` is used to redact decoded tool arguments once more before validation.
    """
    if unit.entry is not None or message_type(unit.message) == "tool-calls":
        return _tool_call(unit, secrets)
    if is_transcript(unit.message):
        return _transcript(unit.message)
    return OtherUnit(vapi_type=message_type(unit.message))

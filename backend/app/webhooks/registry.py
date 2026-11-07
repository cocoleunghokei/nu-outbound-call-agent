"""Handler registries: how owner packages plug logic into the webhook ingress.

Owners register from their package's ``register(reg)``:

- ``reg.tools.register_tool_handler(name, fn)``: ``ask_user`` and ``report_decision``
  (Coco), ``report_outcome`` (Harper). A tool with no handler is stored ``pending`` and run
  later by ``dispatch_pending`` once one is registered.
- ``reg.events.register_event_handler(vapi_type, fn)``: ``status-update`` and
  ``speech-update`` (Coco), ``end-of-call-report`` (Harper). A type with no handler is kept
  in ``vapi_raw_events`` only.

Handlers run inside the ingress transaction under their own savepoint, after the call lock
is held. They must not commit, must not call the network (500 ms ack budget), and must never
store ``confidence``/``dimensions``: the ingress writes those to ``agent_scores`` itself.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.webhooks.bind import BoundCall


@dataclass(frozen=True)
class ToolCallContext:
    """One tool call from the in-call model, validated against ``tools/<name>.args``.

    ``arguments`` is redacted and has ``confidence`` and ``dimensions`` removed (DC-5): the
    ingress stores those in ``agent_scores``. ``source_ts`` is Vapi's message timestamp.
    """

    call_id: UUID
    user_id: UUID
    tool_call_id: str
    name: str
    arguments: dict[str, Any] = field(repr=False)
    source_ts: datetime | None


ToolHandler = Callable[[AsyncConnection[Any], ToolCallContext], Awaitable[str]]
EventHandler = Callable[[AsyncConnection[Any], BoundCall, dict[str, Any]], Awaitable[None]]


class ToolRegistry:
    """Tool name -> handler. The handler's string result is returned to Vapi."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register_tool_handler(self, name: str, fn: ToolHandler) -> None:
        """Register ``fn`` for tool ``name``, replacing any earlier handler."""
        self._handlers[name] = fn

    def get(self, name: str) -> ToolHandler | None:
        """Return the handler for ``name``, or None."""
        return self._handlers.get(name)

    def names(self) -> list[str]:
        """Return every tool name that has a handler."""
        return sorted(self._handlers)


class EventRegistry:
    """Vapi message type -> handler. The handler gets the redacted body."""

    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}

    def register_event_handler(self, vapi_type: str, fn: EventHandler) -> None:
        """Register ``fn`` for Vapi message type ``vapi_type``, replacing any earlier one."""
        self._handlers[vapi_type] = fn

    def get(self, vapi_type: str) -> EventHandler | None:
        """Return the handler for ``vapi_type``, or None (the event is then raw-only)."""
        return self._handlers.get(vapi_type)

"""In-memory voice platform for tests and local development."""

from datetime import datetime
from typing import Any
from uuid import UUID

from app.voice.port import CreatedCall, prepare_assistant
from app.voice.prompt_safety import Snapshot


class FakeVoicePlatform:
    """Records calls and controls; ``fail_next`` scripts an exception for the next operation."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.controls: list[tuple[str, dict[str, Any]]] = []
        self._created: list[tuple[CreatedCall, dict[str, Any], datetime | None]] = []
        self._failures: list[Exception] = []

    def fail_next(self, exc: Exception) -> None:
        """Make the next create/get/control/find call raise ``exc`` (nothing is recorded)."""
        self._failures.append(exc)

    def _maybe_fail(self) -> None:
        if self._failures:
            raise self._failures.pop(0)

    async def create_call(
        self,
        assistant: dict[str, Any],
        customer_e164: str,
        metadata: dict[str, Any],
        snapshot: Snapshot,
    ) -> CreatedCall:
        config = prepare_assistant(assistant, snapshot)
        self._maybe_fail()
        n = len(self.calls) + 1
        created = CreatedCall(f"fake-{n}", f"https://fake.invalid/control/{n}", None)
        record = {
            "assistant": config,
            "customer": customer_e164,
            "metadata": dict(metadata),
            "created": created,
        }
        self.calls.append(record)
        return created

    async def get_call(self, vapi_call_id: str) -> dict[str, Any]:
        self._maybe_fail()
        for record in self.calls:
            if record["created"].vapi_call_id == vapi_call_id:
                return {"id": vapi_call_id, "metadata": record["metadata"]}
        return {"id": vapi_call_id}

    async def send_control(self, control_url: str, message: dict[str, Any]) -> None:
        self._maybe_fail()
        self.controls.append((control_url, message))

    async def find_call_by_app_id(
        self, call_id: UUID, created_after: datetime
    ) -> CreatedCall | None:
        self._maybe_fail()
        for record in self.calls:
            if str(record["metadata"].get("call_id")) == str(call_id):
                created: CreatedCall = record["created"]
                return created
        return None

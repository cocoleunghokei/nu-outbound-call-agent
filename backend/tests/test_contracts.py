"""Tests for the JSON Schema contracts in contracts/ and their packaged copies."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.contracts import load_schema, validator_for
from app.voice.tools import tool_definitions

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"
EXAMPLES = CONTRACTS / "examples"
BLUEPRINT_EVENT_TYPES = {
    "status.changed",
    "transcript.final",
    "ping.opened",
    "ping.resolved",
    "agent.decision",
    "field.shared",
    "command.applied",
    "call.ended",
}


def schema_names() -> list[str]:
    """Schema names relative to contracts/, e.g. 'payloads/status.changed.v1.json'."""
    return sorted(p.relative_to(CONTRACTS).as_posix() for p in CONTRACTS.rglob("*.v1.json"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_valid(name: str, instance: Any) -> bool:
    return validator_for(name).is_valid(instance)


def call_event(event_type: str, payload: Any) -> dict[str, Any]:
    return {
        "event_id": "6f1f1b2e-6a53-4b8e-9d52-0d5a3a1c0001",
        "call_id": "6f1f1b2e-6a53-4b8e-9d52-0d5a3a1c0002",
        "seq": 1,
        "type": event_type,
        "occurred_at": "2026-11-09T18:00:00.000Z",
        "schema_version": 1,
        "payload": payload,
    }


def test_schemas_are_valid_2020_12() -> None:
    names = schema_names()
    assert len(names) >= 18
    for name in names:
        Draft202012Validator.check_schema(read_json(CONTRACTS / name))


def test_examples_validate() -> None:
    checked = 0
    for name in schema_names():
        validator = validator_for(name)
        valid = EXAMPLES / f"{name.removesuffix('.json')}.valid.json"
        invalid = EXAMPLES / f"{name.removesuffix('.json')}.invalid.json"
        assert valid.exists(), f"missing {valid}"
        assert invalid.exists(), f"missing {invalid}"
        assert validator.is_valid(read_json(valid)), f"{valid} should be valid"
        assert not validator.is_valid(read_json(invalid)), f"{invalid} should be invalid"
        checked += 1
    assert checked == len(schema_names())


def test_call_event_examples_per_type_validate() -> None:
    files = sorted((EXAMPLES / "events").glob("*.valid.json"))
    types = {read_json(f)["type"] for f in files}
    assert types == BLUEPRINT_EVENT_TYPES
    for f in files:
        assert is_valid("call-event.v1.json", read_json(f)), f


def test_call_event_types_match_blueprint() -> None:
    schema = load_schema("call-event.v1.json")
    types = {branch["if"]["properties"]["type"]["const"] for branch in schema["allOf"]}
    assert types == BLUEPRINT_EVENT_TYPES
    assert schema["properties"]["type"]["x-open-enum"] is True
    for event_type in types:
        assert (CONTRACTS / "payloads" / f"{event_type}.v1.json").exists()


def test_agent_decision_payload_rejects_score_fields() -> None:
    payload = {"situation": "They offered 4pm", "action": "decline", "reason": "After 3pm"}
    assert is_valid("payloads/agent.decision.v1.json", payload)
    for extra in ({"confidence": "high"}, {"dimensions": {"understood": True}}):
        assert not is_valid("payloads/agent.decision.v1.json", {**payload, **extra})


def test_tool_definitions_wrap_args_schemas() -> None:
    definitions = {d["function"]["name"]: d for d in tool_definitions()}
    assert set(definitions) == {"ask_user", "report_decision", "report_outcome"}
    for name, definition in definitions.items():
        assert definition["type"] == "function"
        assert definition["async"] is True
        assert definition["function"]["description"]
        assert definition["function"]["parameters"] == load_schema(f"tools/{name}.args.v1.json")
        assert definition["function"]["parameters"] == read_json(
            CONTRACTS / "tools" / f"{name}.args.v1.json"
        )


def test_tool_dimensions_and_confidence_tiers() -> None:
    dims = {
        "needs_ask_first_field",
        "involves_money",
        "meets_constraints",
        "matches_fallback",
        "answerable_from_brief",
        "understood",
    }
    for name in ("ask_user", "report_decision"):
        schema = load_schema(f"tools/{name}.args.v1.json")
        assert schema["properties"]["confidence"]["enum"] == ["high", "medium", "low"]
        assert "dimensions" in schema["required"]
        assert set(schema["properties"]["dimensions"]["required"]) == dims


def test_generated_ts_has_open_enums() -> None:
    text = (REPO_ROOT / "frontend" / "src" / "contracts" / "generated.ts").read_text()
    assert "| (string & {})" in text
    block = text.split("export interface StatusChangedPayload {")[1].split("\n}")[0]
    assert "(string & {})" in block


def test_consumer_type_check_exists() -> None:
    text = (REPO_ROOT / "frontend" / "src" / "contracts" / "typecheck.ts").read_text()
    assert "@ts-expect-error" in text
    assert "parseCallEvent" in text


def test_envelope_binds_payload_to_type() -> None:
    status_payload = {"status": "dialing"}
    assert not is_valid("call-event.v1.json", call_event("transcript.final", status_payload))
    assert is_valid("call-event.v1.json", call_event("status.changed", status_payload))
    assert is_valid("call-event.v1.json", call_event("future.thing", {"anything": 1}))
    assert not is_valid("call-event.v1.json", call_event("future.thing", None))


def test_brief_rejects_profile_values() -> None:
    schema = load_schema("brief.v1.json")
    assert schema["additionalProperties"] is False
    valid = read_json(EXAMPLES / "brief.v1.valid.json")
    assert is_valid("brief.v1.json", valid)
    assert not is_valid("brief.v1.json", {**valid, "profile_values": {"full_name": "Alex Chen"}})
    assert not is_valid("brief.v1.json", {**valid, "share_freely_values": {"full_name": "x"}})
    task_vars = {"name": {"literal": "Alex", "value": "x"}}
    assert not is_valid("brief.v1.json", {**valid, "task_vars": task_vars})
    assert not is_valid("brief.v1.json", {**valid, "task_line": "x" * 121})


def test_ping_reply_and_command_shapes() -> None:
    assert is_valid("commands/ping-reply.v1.json", {"choice": 3})
    assert not is_valid("commands/ping-reply.v1.json", {"choice": 4})
    assert not is_valid("commands/ping-reply.v1.json", {"choice": 0, "text": "x"})
    assert not is_valid("commands/ping-reply.v1.json", {"text": ""})
    assert is_valid("commands/call-command.v1.json", {"type": "pause"})
    assert not is_valid("commands/call-command.v1.json", {"type": "instruction"})
    assert is_valid("commands/call-command.v1.json", {"type": "instruction", "text": "Ask Fri"})


def test_error_shape_has_no_extra_top_level_keys() -> None:
    error = {"code": "ping_closed", "message": "Too late", "retryable": False}
    assert is_valid("error.v1.json", {"error": error})
    assert not is_valid("error.v1.json", {"error": error, "detail": "x"})


def test_schemas_load_from_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT / "backend")
    assert os.getcwd() == str(REPO_ROOT / "backend")
    schema = load_schema("call-event.v1.json")
    assert schema["$id"] == "call-event.v1.json"
    validator = validator_for("call-event.v1.json")
    assert validator.is_valid(call_event("status.changed", {"status": "talking"}))


def test_packaged_schemas_match_contracts() -> None:
    for name in schema_names():
        assert load_schema(name) == read_json(CONTRACTS / name)

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


def test_agent_decision_payload_accepts_missed_preference_but_rejects_scores() -> None:
    payload = {
        "situation": "They offered 7pm on the patio",
        "action": "accept",
        "reason": "Meets the constraints",
        "missed_preference": "No window seat, patio only",
    }
    assert is_valid("payloads/agent.decision.v1.json", payload)
    too_long = {**payload, "missed_preference": "x" * 201}
    assert not is_valid("payloads/agent.decision.v1.json", too_long)
    assert not is_valid("payloads/agent.decision.v1.json", {**payload, "missed_preference": 3})
    for extra in ({"confidence": "medium"}, {"dimensions": {"understood": True}}):
        assert not is_valid("payloads/agent.decision.v1.json", {**payload, **extra})


def test_ping_opened_payload_rejects_score_fields() -> None:
    ping = read_json(EXAMPLES / "payloads" / "ping.opened.v1.valid.json")
    assert is_valid("payloads/ping.opened.v1.json", ping)
    for extra in ({"confidence": "low"}, {"dimensions": {"understood": True}}):
        assert not is_valid("payloads/ping.opened.v1.json", {**ping, **extra})
        assert not is_valid("ping.v1.json", {**ping, **extra})


def test_field_shared_state_is_closed_enum() -> None:
    for state in ("approved", "sent_to_agent", "spoken"):
        assert is_valid("payloads/field.shared.v1.json", {"field_key": "k", "state": state})
    assert not is_valid("payloads/field.shared.v1.json", {"field_key": "k", "state": "shared"})


def test_tool_definitions_wrap_args_schemas() -> None:
    all_tools = tool_definitions()
    assert all_tools[-1] == {"type": "endCall"}
    definitions = {d["function"]["name"]: d for d in all_tools[:-1]}
    assert set(definitions) == {"ask_user", "report_decision", "report_outcome"}
    for name, definition in definitions.items():
        assert definition["type"] == "function"
        assert definition["async"] is True
        assert definition["function"]["description"]
        assert definition["function"]["parameters"] == load_schema(f"tools/{name}.args.v1.json")
        assert definition["function"]["parameters"] == read_json(
            CONTRACTS / "tools" / f"{name}.args.v1.json"
        )


def _description(name: str) -> str:
    tools = {d["function"]["name"]: d for d in tool_definitions() if "function" in d}
    return str(tools[name]["function"]["description"])


def test_report_decision_description_allows_a_low_counter() -> None:
    """Regression: it said "call ask_user whenever the score is Low", so counters went unlogged."""
    text = _description("report_decision")
    assert "Call ask_user instead whenever the score is Low" not in text
    assert "action counter and confidence low" in text
    assert "before you speak" in text


def test_ask_user_description_covers_missing_info_and_not_understood() -> None:
    """Regression: it listed only constraint, ask-first and money, so missing info never pinged."""
    text = _description("ask_user")
    assert "not in the brief" in text
    assert "can't understand them after asking once" in text
    assert "before you speak" in text


# --- structured rules and offers (docs/superpowers/plans/2026-11-18-structured-offers.md) ---

BRIEF_ITEM_LISTS = ("constraints", "preferences", "fallbacks")


def _brief_with(list_name: str, item: dict[str, Any]) -> dict[str, Any]:
    brief = read_json(EXAMPLES / "brief.v1.valid.json")
    brief[list_name] = [item]
    return brief


@pytest.mark.parametrize("list_name", BRIEF_ITEM_LISTS)
def test_brief_items_take_optional_rules(list_name: str) -> None:
    assert is_valid("brief.v1.json", _brief_with(list_name, {"text": "Before 3pm"}))
    before_3pm = {"text": "Before 3pm", "rules": [{"kind": "time", "latest": "14:59"}]}
    assert is_valid("brief.v1.json", _brief_with(list_name, before_3pm))
    friday_morning = {
        "text": "Friday 20 November, morning",
        "rules": [{"kind": "date", "on": "2026-11-20"}, {"kind": "time", "latest": "11:59"}],
    }
    assert is_valid("brief.v1.json", _brief_with(list_name, friday_morning))


def test_never_agree_items_take_no_rules() -> None:
    item = {"text": "A deposit", "rules": [{"kind": "person", "name": "x"}]}
    assert not is_valid("brief.v1.json", _brief_with("never_agree", item))


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "date", "on": "2026-11-19"},
        {"kind": "date", "earliest": "2026-11-19", "latest": "2026-11-20"},
        {"kind": "time", "earliest": "18:30", "latest": "20:00"},
        {"kind": "time", "latest": "00:00"},
        {"kind": "time", "earliest": "23:59"},
        {"kind": "party_size", "exactly": 6},
        {"kind": "party_size", "min": 2, "max": 4},
        {"kind": "person", "name": "Dr. Rivera"},
    ],
)
def test_valid_rules(rule: dict[str, Any]) -> None:
    assert is_valid("brief-rule.v1.json", rule)


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "time", "latest": "3pm"},  # must be 24-hour HH:MM
        {"kind": "time", "latest": "24:00"},
        {"kind": "time", "latest": "9:00"},
        {"kind": "date", "on": "19 November"},
        {"kind": "date", "on": "2026-11-19", "at": "x"},  # unknown key
        {"kind": "party_size", "exactly": 0},
        {"kind": "person"},  # a person rule needs a name
        {"kind": "price", "max": 50},  # not a rule kind (money is a gate, not a rule)
        {"latest": "14:59"},  # no kind
    ],
)
def test_invalid_rules(rule: dict[str, Any]) -> None:
    assert not is_valid("brief-rule.v1.json", rule)


@pytest.mark.parametrize("tool", ["ask_user", "report_decision"])
def test_tools_take_an_optional_offer_written_out_in_full(tool: str) -> None:
    schema = load_schema(f"tools/{tool}.args.v1.json")
    # The tool schema is sent to the model as is, so the offer can't be a $ref.
    assert "$ref" not in json.dumps(schema)
    assert "offer" not in schema.get("required", [])
    name = f"tools/{tool}.args.v1.json"
    base = read_json(EXAMPLES / "tools" / f"{tool}.args.v1.valid.json")
    assert is_valid(name, {**base, "offer": {"date": "2026-11-19", "time": "15:00"}})
    assert is_valid(name, {**base, "offer": {"party_size": 6, "person": "Dr. Patel"}})
    assert is_valid(name, {**base, "offer": {}})
    assert not is_valid(name, {**base, "offer": {"time": "3pm"}})
    assert not is_valid(name, {**base, "offer": {"time": "15:00", "meets": True}})


def test_both_tools_define_the_same_offer() -> None:
    ask = load_schema("tools/ask_user.args.v1.json")["properties"]["offer"]
    decide = load_schema("tools/report_decision.args.v1.json")["properties"]["offer"]
    assert ask == decide


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
    assert "status: CallStatus;" in block
    alias = text.split("export type CallStatus =")[1].split(";")[0]
    assert "(string & {})" in alias
    ping_state = text.split("export type PingState =")[1].split(";")[0]
    assert "(string & {})" in ping_state


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


def test_call_ended_accepts_failed_and_no_answer_outcomes() -> None:
    """PO-1: Failed and no-answer are call outcomes, not only a status."""
    for outcome in ("failed", "no_answer", "booked", None):
        assert is_valid("payloads/call.ended.v1.json", {"outcome": outcome, "ended_reason": "x"})


# Owner code imports these names from app.contracts.generated. Each comes from a `title` in
# contracts/ (or, for CallEventType, the envelope's if/then branches), so a schema change
# cannot silently renumber or rename them. Renaming one is a contract change.
PINNED_STR_ENUMS: dict[str, set[str]] = {
    "CallEventType": BLUEPRINT_EVENT_TYPES,
    "CallStatus": {
        "creating",
        "creation_uncertain",
        "dialing",
        "in_phone_menu",
        "talking",
        "waiting_on_you",
        "on_hold",
        "ended",
        "failed",
    },
    "PingState": {"open", "answered", "expired", "superseded", "cancelled"},
    "CallOutcome": {
        "booked",
        "cancelled",
        "rescheduled",
        "needs_follow_up",
        "refused_ai",
        "wrong_number",
        "failed",
        "no_answer",
    },
    "ReportedOutcome": {
        "booked",
        "cancelled",
        "rescheduled",
        "needs_follow_up",
        "refused_ai",
        "wrong_number",
    },
    "EventSource": {"vapi", "backend", "user"},
    "Confidence": {"high", "medium", "low"},
    "DecisionAction": {
        "accept",
        "counter",
        "decline",
        "share_field",
        "take_fallback",
        "end_call",
    },
    "PingKind": {"decision", "field_approval"},
    "DisclosureState": {"approved", "sent_to_agent", "spoken"},
    "TranscriptRole": {"agent", "business", "user"},
    "CallCommandType": {"instruction", "pause", "resume", "end"},
}
PINNED_MODELS = [
    "ReportOutcomeArgs",
    "AskUserArgs",
    "ReportDecisionArgs",
    "OutcomeDetails",
    "Dimensions",
    "PingReplyChoice",
    "PingReplyText",
    "TaskVarLiteral",
    "TaskVarFieldRef",
    "ErrorBody",
]


@pytest.mark.parametrize("name", sorted(PINNED_STR_ENUMS))
def test_generated_enum_names_are_pinned_str_enums(name: str) -> None:
    from enum import StrEnum

    from app.contracts import generated

    enum = getattr(generated, name, None)
    assert enum is not None, f"generated.py lost the stable name {name}"
    assert issubclass(enum, StrEnum)
    assert {member.value for member in enum} == PINNED_STR_ENUMS[name]


@pytest.mark.parametrize("name", PINNED_MODELS)
def test_generated_model_names_are_pinned(name: str) -> None:
    from pydantic import BaseModel

    from app.contracts import generated

    model = getattr(generated, name, None)
    assert isinstance(model, type) and issubclass(model, BaseModel), name


def test_generated_models_use_pinned_types() -> None:
    from app.contracts import generated as g

    fields = g.ReportOutcomeArgs.model_fields
    assert fields["outcome"].annotation is g.ReportedOutcome
    assert g.PingResolvedPayload.model_fields["state"].annotation is g.PingState
    assert g.Ping.model_fields["state"].annotation is g.PingState
    assert g.StatusChangedPayload.model_fields["status"].annotation is g.CallStatus
    assert g.CallEndedPayload.model_fields["outcome"].annotation == g.CallOutcome | None


def test_no_numbered_or_generic_generated_names() -> None:
    """Names such as Model, Type, State1 or Outcome1 mean a sub-schema is missing a title."""
    import re

    text = (Path(__file__).parents[1] / "app" / "contracts" / "generated.py").read_text()
    classes = re.findall(r"^class (\w+)\(", text, re.MULTILINE)
    assert not [c for c in classes if re.search(r"\d$", c)], classes
    generic = {"Model", "Type", "State", "Status", "Outcome", "Kind", "Role", "Source", "Action"}
    assert not generic & set(classes), classes
    assert "(Enum)" not in text, "every string enum should generate as StrEnum"

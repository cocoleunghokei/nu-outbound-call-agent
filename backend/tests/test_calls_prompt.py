"""The agent's system prompt and assistant config (DC-1, DC-4, DC-6). No database; fake data."""

import asyncio
import json
from typing import Any

import pytest

from app.calls.assistant import build_assistant_config
from app.calls.prompt import GUARDRAIL_RULES, build_system_prompt
from app.contracts.generated import Brief
from app.voice.fake import FakeVoicePlatform
from app.voice.port import prepare_assistant
from app.voice.prompt_safety import AskFirstInPrompt, MissingVar, Snapshot

FAKE_DOB = "March 4th, 2004"
FAKE_INSURANCE = "BCB-4471-0923"
SNAPSHOT = Snapshot.from_rows(
    [
        ("calling_name", "Alex", "share_freely"),
        ("full_name", "Alex Chen", "share_freely"),
        ("email", "alex@example.test", "share_freely"),
        ("date_of_birth", FAKE_DOB, "ask_first"),
        ("insurance_member_id", FAKE_INSURANCE, "ask_first"),
    ]
)


def make_brief(**overrides: Any) -> Brief:
    data: dict[str, Any] = {
        "goal": "Move the Thursday appointment with Dr. Lee to before 3pm",
        "business_name": "Brookline Family Medicine",
        "phone_e164": "+16175550142",
        "template_key": "doctor_reschedule",
        "task_line": "I'd like to reschedule an appointment with {provider_name}.",
        "task_vars": {"provider_name": {"literal": "Dr. Lee"}},
        "constraints": [{"text": "Before 3pm"}],
        "preferences": [{"text": "In person, not a video visit"}],
        "fallbacks": [{"text": "Fri morning"}, {"text": "Tue afternoon"}],
        "never_agree": [{"text": "A cancellation fee"}],
        "share_freely_keys": ["full_name", "email"],
        "ask_first_keys": ["date_of_birth", "insurance_member_id"],
    }
    data.update(overrides)
    return Brief.model_validate(data)


def config(brief: Brief | None = None, snapshot: Snapshot = SNAPSHOT) -> dict[str, Any]:
    return build_assistant_config(
        brief or make_brief(),
        snapshot,
        server_url="https://api.example.test/webhooks/vapi",
        webhook_secret="whsec-fake-for-tests",
        max_duration_s=900,
    )


# --- the prompt -----------------------------------------------------------------------------


def test_prompt_carries_the_brief() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "Brookline Family Medicine" in prompt and "on behalf of Alex" in prompt
    assert "Your goal: Move the Thursday appointment with Dr. Lee to before 3pm" in prompt
    assert "- Before 3pm" in prompt
    assert "- In person, not a video visit" in prompt
    assert "1. Fri morning\n2. Tue afternoon" in prompt  # ranked, best first
    assert "- A cancellation fee" in prompt


def test_prompt_shares_share_freely_values_only() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "email: alex@example.test" in prompt
    assert "full name: Alex Chen" in prompt
    assert FAKE_DOB not in prompt and FAKE_INSURANCE not in prompt


def test_prompt_names_ask_first_fields_without_their_values() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "date of birth (field_requested: date_of_birth)" in prompt
    assert "insurance member id (field_requested: insurance_member_id)" in prompt


def test_an_ask_first_field_missing_from_the_brief_is_still_named() -> None:
    brief = make_brief(ask_first_keys=[])
    assert "field_requested: date_of_birth" in build_system_prompt(brief, SNAPSHOT)


def test_a_share_freely_key_not_in_the_brief_is_not_shared() -> None:
    brief = make_brief(share_freely_keys=["full_name"])
    assert "alex@example.test" not in build_system_prompt(brief, SNAPSHOT)


def test_prompt_has_the_dc4_rubric() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    for text in (
        "Two gates",
        "Does it need an ask-first field?",
        "Does it involve money, a deposit, a fee or a never-agree item?",
        "Four checks",
        "Does the offer meet every hard constraint?",
        "Does it match a listed fallback?",
        "Can you answer from this brief without making anything up?",
        "Did you understand what they said?",
        "There is no numeric threshold.",
        "- High:",
        "- Medium:",
        "missed_preference",
        "- Low:",
        "Re-check every counter-offer.",
        "Never say a confidence level or score out loud.",
    ):
        assert text in prompt, text


def test_prompt_explains_waiting_and_the_no_reply_rule() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "WHILE YOU WAIT FOR ALEX" in prompt
    assert "quotes your question" in prompt
    assert "did not reply in time" in prompt and "Never guess." in prompt


def test_prompt_includes_the_guardrail_rules() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    for rule in GUARDRAIL_RULES:
        assert f"- {rule}" in prompt


def test_empty_lists_say_none() -> None:
    brief = make_brief(preferences=[], fallbacks=[], never_agree=[])
    prompt = build_system_prompt(brief, SNAPSHOT)
    assert (
        "PREFERENCES (try to meet them; missing one is allowed but must be reported):\n- (none)"
        in prompt
    )
    assert "FALLBACKS (acceptable alternatives, best first):\n- (none)" in prompt


# --- the assistant config ---------------------------------------------------------------------


def test_opening_line_is_the_fixed_dc1_template() -> None:
    assert config()["firstMessage"] == (
        "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. "
        "I'd like to reschedule an appointment with Dr. Lee."
    )
    assert config()["firstMessageMode"] == "assistant-speaks-first"


def test_config_is_transcribed_never_recorded_and_passes_the_port_checks() -> None:
    built = config()
    assert built["artifactPlan"] == {
        "recordingEnabled": False,
        "transcriptPlan": {"enabled": True},
    }
    prepared = prepare_assistant(built, SNAPSHOT)
    names = [t.get("function", {}).get("name") or t["type"] for t in prepared["model"]["tools"]]
    assert names == ["ask_user", "report_decision", "report_outcome", "endCall"]


def test_config_points_vapi_at_our_webhook_with_the_secret() -> None:
    built = config()
    assert built["server"] == {
        "url": "https://api.example.test/webhooks/vapi",
        "headers": {"X-Vapi-Secret": "whsec-fake-for-tests"},
    }
    assert set(built["serverMessages"]) >= {"status-update", "speech-update", "tool-calls"}
    assert built["maxDurationSeconds"] == 900
    assert {k: built["model"][k] for k in ("provider", "model")} == {
        "provider": "deep-seek",
        "model": "deepseek-chat",
    }
    assert built["monitorPlan"] == {"listenEnabled": True, "controlEnabled": True}


def test_no_ask_first_value_anywhere_in_what_reaches_vapi() -> None:
    voice = FakeVoicePlatform()
    asyncio.run(voice.create_call(config(), "+16175550142", {"call_id": "c1"}, SNAPSHOT))
    sent = json.dumps(voice.calls[0]["assistant"])
    assert FAKE_DOB not in sent and FAKE_INSURANCE not in sent


def test_missing_calling_name_blocks_the_call() -> None:
    snapshot = Snapshot.from_rows([("email", "alex@example.test", "share_freely")])
    with pytest.raises(MissingVar):
        config(snapshot=snapshot)


def test_a_task_variable_without_a_value_blocks_the_call() -> None:
    brief = make_brief(task_vars={"provider_name": {"field_ref": "doctor_name"}})
    with pytest.raises(MissingVar):
        config(brief)


def test_an_ask_first_field_in_the_opening_line_blocks_the_call() -> None:
    brief = make_brief(
        task_line="Calling about {dob}.", task_vars={"dob": {"field_ref": "date_of_birth"}}
    )
    with pytest.raises(AskFirstInPrompt):
        config(brief)


def test_an_ask_first_value_typed_into_the_brief_blocks_the_call() -> None:
    brief = make_brief(constraints=[{"text": f"Patient born {FAKE_DOB}"}])
    with pytest.raises(AskFirstInPrompt):
        config(brief)

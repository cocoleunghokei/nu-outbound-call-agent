"""The agent's system prompt and assistant config (DC-1, DC-4, DC-6). No database; fake data."""

import asyncio
import json
from typing import Any

import pytest

from app.calls.assistant import TEMPERATURE, build_assistant_config
from app.calls.prompt import GUARDRAIL_RULES, build_system_prompt, opening_request
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
    assert "<goal>Move the Thursday appointment with Dr. Lee to before 3pm</goal>" in prompt
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


def test_a_low_offer_gets_one_counter_before_a_ping() -> None:
    # Q9 in evals/incall/CASES.md: counter once inside the rules, then ping; never accept Low.
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "Never accept a Low offer on your own." in prompt
    assert "counter once first" in prompt
    assert "call report_decision with action counter and confidence low" in prompt
    assert "Counter only once per offer." in prompt
    # Found by the eval: the counter was logged a turn late (C4-03), and the agent asked
    # again after "we can't seat 6 at all this weekend" (C4-11).
    assert "call report_decision with action counter and confidence low. Counter" in prompt
    assert "call ask_user straight away; do not ask the business again" in prompt
    assert "neither gate applies, counter once first" in prompt  # money and ask-first still ping


def test_every_action_is_logged_and_checking_always_pings() -> None:
    # Found by the in-call eval: it countered or accepted without report_decision, and said
    # "Let me check with Alex" without calling ask_user, so the user was never pinged.
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "call report_decision.\nHow to call a tool: call it first, before you say anything." in (
        prompt
    )
    assert "Never say the words of an action" in prompt
    assert "Never say you are checking with Alex unless you called ask_user first." in prompt
    assert "under 80 characters" in prompt


def test_tools_are_called_before_speaking() -> None:
    # Found by the eval (C4-03, C4-12, C4-13): DeepSeek either talks or calls a tool in one
    # reply, never both, so "in the same reply as your words" left every counter unlogged.
    # It does call a tool first and then speak after the result, as it already did for pings.
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "same reply" not in prompt
    assert "You speak right after its result" in prompt


def test_missing_info_and_unclear_speech_ping_instead_of_asking_again() -> None:
    # Found by the eval: after "we need a referral" it asked the clinic again (C4-12), and it
    # asked a garbled caller to repeat a second time (C4-13), never pinging Alex.
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "If the business needs it to go on, call ask_user." in prompt
    assert "say so or call ask_user" not in prompt
    assert "call ask_user with understood false; do not ask them a third time" in prompt
    assert "(for example a referral), or after your one counter, call ask_user straight away" in (
        prompt.replace("\n", " ")
    )


def test_it_confirms_and_waits_for_goodbye_before_hanging_up() -> None:
    # Found by the eval (C4-10): "Saturday 7pm works" and the agent booked it and hung up,
    # before the business said "Sorry, 7 just went". Decided in Q12 (CASES.md).
    prompt = build_system_prompt(make_brief(), SNAPSHOT).replace("\n", " ")
    assert '"7 works" is not a booking yet' in prompt
    assert "read the result back in one sentence" in prompt
    assert "End the call only after the business says goodbye" in prompt
    assert 'a clear wrap-up ("You\'re all set", "Anything else?")' in prompt
    assert "You may end without waiting only when the business hangs up" in prompt


def test_inventing_includes_saying_a_detail_does_not_exist() -> None:
    assert any('"no referral"' in rule and "inventing too" in rule for rule in GUARDRAIL_RULES)


def test_a_refusal_to_talk_to_an_ai_gets_one_polite_ask_then_refused_ai() -> None:
    # Q10 in evals/incall/CASES.md.
    rule = next(r for r in GUARDRAIL_RULES if "refused_ai" in r)
    assert "say once, politely, that you are an AI" in rule
    assert "If they refuse again, or refuse outright" in rule


def test_prompt_explains_waiting_and_the_no_reply_rule() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "<while_you_wait>" in prompt and "</while_you_wait>" in prompt
    assert "quotes your question" in prompt
    assert "did not reply in time" in prompt and "Never guess." in prompt


def test_prompt_includes_the_guardrail_rules() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    for rule in GUARDRAIL_RULES:
        assert f"- {rule}" in prompt


def test_empty_lists_say_none() -> None:
    brief = make_brief(preferences=[], fallbacks=[], never_agree=[])
    prompt = build_system_prompt(brief, SNAPSHOT)
    assert "<preferences>\n- (none)\n</preferences>" in prompt
    assert "<fallbacks>\n- (none)\n</fallbacks>" in prompt
    assert "<never_agree>\n- (none)\n</never_agree>" in prompt


def test_every_section_has_one_xml_tag_and_the_brief_holds_the_typed_lists() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    sections = (
        "brief", "goal", "first_sentence_asked_for", "share_freely", "call_details",
        "ask_first", "hard_constraints", "preferences", "fallbacks", "never_agree",
        "using_the_brief", "deciding", "while_you_wait", "finishing", "rules",
    )  # fmt: skip
    for tag in sections:
        # Prose may name a tag ("<rules>"), but only the section itself closes it.
        assert f"<{tag}>" in prompt and prompt.count(f"</{tag}>") == 1, tag
    brief = prompt[prompt.index("<brief>") : prompt.index("</brief>")]
    assert "- Before 3pm" in brief and "1. Fri morning" in brief
    assert prompt.index("</brief>") < prompt.index("</using_the_brief>") < prompt.index("</rules>")


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


def test_config_pins_a_low_temperature_that_survives_the_port() -> None:
    built = config()
    assert built["model"]["temperature"] == TEMPERATURE
    assert 0 <= TEMPERATURE <= 0.3
    assert prepare_assistant(built, SNAPSHOT)["model"]["temperature"] == TEMPERATURE


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


# --- Q2 and Q8 (backend/evals/incall/CASES.md) ----------------------------------------------


def test_the_opening_line_is_quoted_but_sets_no_priority() -> None:
    # Q8, revised: only the typed lists say what is a must or a nice to have. The opening line
    # used to count as a hard constraint, which overruled "With Dr. Rivera" typed as a
    # preference (C4-06).
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert (
        "<first_sentence_asked_for>I'd like to reschedule an appointment with Dr. Lee."
        "</first_sentence_asked_for>"
    ) in prompt
    assert "is also a hard constraint" not in prompt
    assert "It is not a rule: it never makes anything a must or a nice to have." in (
        prompt.replace("\n", " ")
    )
    assert "say what changed in the reason" in prompt.replace("\n", " ")


def test_opening_request_resolves_share_freely_field_refs() -> None:
    brief = make_brief(
        task_line="Book a table for {party_size} under {full_name}.",
        task_vars={"party_size": {"literal": "6"}, "full_name": {"field_ref": "full_name"}},
    )
    assert opening_request(brief, SNAPSHOT) == "Book a table for 6 under Alex Chen."


def test_opening_request_is_left_out_when_a_variable_is_missing() -> None:
    brief = make_brief(task_line="Book a table for {party_size}.", task_vars={})
    assert opening_request(brief, SNAPSHOT) is None
    prompt = build_system_prompt(brief, SNAPSHOT)
    assert "Your first sentence has already been said for you.\n" in prompt
    assert "It asked for" not in prompt


def test_opening_request_never_resolves_an_ask_first_field() -> None:
    brief = make_brief(
        task_line="Born {date_of_birth}.",
        task_vars={"date_of_birth": {"field_ref": "date_of_birth"}},
    )
    with pytest.raises(AskFirstInPrompt):
        opening_request(brief, SNAPSHOT)


def test_low_pings_must_carry_everything_needed_to_decide() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "everything needed to decide from the card alone" in prompt
    assert "what they offered (day, time, who, price) and what Alex asked for" in prompt


def test_a_better_offer_while_waiting_is_taken_right_away() -> None:
    prompt = build_system_prompt(make_brief(), SNAPSHOT)
    assert "If the business speaks without a new offer, say only" in prompt
    assert "take it right away and call report_decision: that withdraws your question" in prompt
    assert "If it is still Low, call ask_user again" in prompt

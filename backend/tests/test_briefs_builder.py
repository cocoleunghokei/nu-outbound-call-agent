"""Brief builder (plan Step 9): the frontend's missing.test.ts and brief.test.ts, on the server.

Same inputs, same outputs as the chat's own tests, plus the template rules (R1 to R10) and the
two builder routes without a database.
"""

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.briefs.builder import (
    CUSTOM_TASK_LINE,
    Draft,
    ProfileField,
    build_brief,
    default_sharing,
    has_profile_value,
    known_profile_fields,
    missing_template_fields,
    with_profile_field,
)
from app.briefs.catalog import (
    TEMPLATES,
    CallTemplate,
    TemplateError,
    get_template,
    load_templates,
    parse_templates,
    templates_json,
)
from app.briefs.router import build_response, validate_brief
from app.config import Settings
from app.contracts import validator_for
from app.main import create_app
from tests.test_auth import ISSUER, SECRET, bearer, make_token

# frontend fakeBriefs.ts fakeProfile (fake data only).
FAKE_PROFILE = [
    ProfileField("full_name", "Alex Chen", "share_freely"),
    ProfileField("calling_name", "Alex", "share_freely"),
    ProfileField("phone", "+16175550142", "share_freely"),
    ProfileField("email", "alex.chen@example.test", "share_freely"),
    ProfileField("date_of_birth", "2004-03-15", "ask_first"),
    ProfileField("address", "123 Example St, Boston, MA", "ask_first"),
]


def template(key: str) -> CallTemplate:
    found = get_template(key)
    assert found is not None, key
    return found


RESTAURANT = template("restaurant_booking")
DOCTOR = template("doctor_booking")


def keys(fields: Any) -> list[str]:
    return [f.key for f in fields]


def without(key: str) -> list[ProfileField]:
    return [f for f in FAKE_PROFILE if f.key != key]


def draft(**overrides: Any) -> Draft:
    """brief.test.ts ``draft()``: a custom call unless overridden."""
    base: dict[str, Any] = {
        "template_key": None,
        "goal": "Ask if they can hold a cake for pickup tomorrow.",
        "business_name": "Sunrise Bakery",
        "phone_e164": "+16175550122",
    }
    base.update(overrides)
    return Draft(**base)


def expect_valid_brief(brief: dict[str, Any], profile: list[ProfileField]) -> None:
    """brief.test.ts ``expectValidBrief``: brief.v1 plus the privacy rule."""
    errors = list(validator_for("brief.v1.json").iter_errors(brief))
    assert errors == [], [e.message for e in errors]
    validate_brief(brief)  # the same check POST /briefs runs, number rules included
    sharing = {f.key: f.sharing for f in profile}
    for spec in brief["task_vars"].values():
        if "field_ref" in spec:
            assert sharing.get(spec["field_ref"]) == "share_freely"
    text = json.dumps(brief)
    for f in profile:
        if f.sharing == "ask_first":
            assert f.value not in text, f"ask-first value of {f.key} leaked"
    assert not set(brief["ask_first_keys"]) & set(brief["share_freely_keys"])


# ---------------------------------------------------------------- templates (catalog)


def test_only_the_two_p0_templates_ship_in_chip_order() -> None:
    assert [t.key for t in TEMPLATES] == ["restaurant_booking", "doctor_booking"]


def test_templates_match_the_frontend_fake_templates() -> None:
    """Same content as frontend fakeBriefs.ts, plus the agreed optional ``suggestions``."""
    assert RESTAURANT.label == "Restaurant: book a table"
    assert RESTAURANT.task_line == "I'd like to book a table for {party_size} on {day} at {time}."
    assert keys(RESTAURANT.required_fields) == [
        "calling_name",
        "phone",
        "party_size",
        "day",
        "time",
    ]
    assert DOCTOR.label == "Doctor: book an appointment"
    assert DOCTOR.task_line == "I'd like to book an appointment with {provider_name}."
    assert keys(DOCTOR.required_fields) == [
        "calling_name",
        "date_of_birth",
        "provider_name",
        "patient_status",
        "reason",
    ]
    assert [f.source for f in DOCTOR.required_fields] == ["profile"] * 2 + ["call"] * 3
    assert RESTAURANT.required_fields[2].suggestions == ("2", "4", "6")


def test_templates_json_round_trips_and_omits_empty_suggestions() -> None:
    data = templates_json()
    assert "suggestions" not in data[0]["required_fields"][0]
    assert data[0]["required_fields"][2]["suggestions"] == ["2", "4", "6"]
    assert parse_templates(copy.deepcopy(data)) == TEMPLATES
    assert load_templates() == TEMPLATES


def good() -> dict[str, Any]:
    return copy.deepcopy(templates_json()[0])


def field(key: str, source: str = "call", question: str = "Which one?") -> dict[str, Any]:
    return {"key": key, "source": source, "question": question}


Mutator = Callable[[dict[str, Any]], None]


def _set(path: str, value: Any) -> Mutator:
    def mutate(t: dict[str, Any]) -> None:
        t[path] = value

    return mutate


def _add_field(f: dict[str, Any]) -> Mutator:
    def mutate(t: dict[str, Any]) -> None:
        t["required_fields"].append(f)

    return mutate


def _set_field(index: int, name: str, value: Any) -> Mutator:
    def mutate(t: dict[str, Any]) -> None:
        t["required_fields"][index][name] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "rule"),
    [
        (_set("key", "Restaurant-Booking"), "R1"),
        (_set("label", ""), "label"),
        (_set("label", "x" * 41), "R2"),
        (_set("task_line", "Book " + "x" * 116), "R3"),
        (_set("task_line", "Book for {party_size} with {pet_name}."), "R4"),
        (_set("task_line", "Book for {party_size}, call {phone}."), "R5"),
        (_set("task_line", "Born {date_of_birth}."), "R4"),
        (_set_field(1, "key", "favourite_colour"), "R7"),
        (_add_field(field("email")), "R8"),
        (_add_field(field("day")), "R8"),
        (_add_field(field("Day")), "R8"),
        (_set_field(3, "question", "Which day"), "R9"),
        (_set_field(3, "question", "Which day? What time?"), "R9"),
        (_set_field(2, "suggestions", ["x" * 201]), "R9"),
        (_set_field(2, "suggestions", ["1", "2", "3", "4", "5", "6"]), "R9"),
        (_set("default_constraints", [{"text": f"Limit {n}"} for n in range(4)]), "R10"),
        (_set("default_constraints", [{"text": "x" * 201}]), "R10"),
        (_set("default_constraints", [{"text": ""}]), "default_constraints"),
        (_set("default_constraints", ["Before 8pm"]), "default_constraints"),
        (_set("enabled", True), "enabled"),
        (_set("required_fields", []), "required_fields"),
        (_set_field(0, "source", "chat"), "source"),
    ],
)
def test_template_rules_reject_a_bad_template(mutate: Mutator, rule: str) -> None:
    bad = good()
    mutate(bad)
    with pytest.raises(TemplateError, match=rule):
        parse_templates([bad])


def test_r5_allows_calling_name_as_a_placeholder() -> None:
    ok = good()
    ok["task_line"] = "Book a table for {party_size} under {calling_name}."
    assert parse_templates([ok])[0].placeholders() == ["party_size", "calling_name"]


def test_r6_allows_an_ask_first_field_to_be_required() -> None:
    assert "date_of_birth" in DOCTOR.profile_keys()


def test_r1_template_keys_are_unique_across_the_list() -> None:
    with pytest.raises(TemplateError, match="R1"):
        parse_templates([good(), good()])


def test_the_file_must_hold_a_list() -> None:
    with pytest.raises(TemplateError):
        parse_templates({"templates": []})


# ---------------------------------------------------------------- missing.test.ts


def test_restaurant_with_a_full_profile_asks_only_for_the_call_details() -> None:
    assert keys(missing_template_fields(RESTAURANT, FAKE_PROFILE, {})) == [
        "party_size",
        "day",
        "time",
    ]


def test_doctor_never_re_asks_profile_fields_including_the_ask_first_dob() -> None:
    missing = keys(missing_template_fields(DOCTOR, FAKE_PROFILE, {}))
    assert missing == ["provider_name", "patient_status", "reason"]
    assert "date_of_birth" not in missing and "calling_name" not in missing


def test_never_asks_for_a_profile_field_the_profile_has_in_any_template() -> None:
    for t in TEMPLATES:
        for f in missing_template_fields(t, FAKE_PROFILE, {}):
            if f.source == "profile":
                assert not has_profile_value(FAKE_PROFILE, f.key)


def test_asks_for_a_profile_field_when_the_profile_lacks_it() -> None:
    assert "date_of_birth" in keys(missing_template_fields(DOCTOR, without("date_of_birth"), {}))


def test_a_blank_profile_value_is_missing() -> None:
    blank_phone = [
        ProfileField(f.key, "  ", f.sharing) if f.key == "phone" else f for f in FAKE_PROFILE
    ]
    assert "phone" in keys(missing_template_fields(RESTAURANT, blank_phone, {}))


def test_each_answered_detail_drops_out_in_template_order() -> None:
    assert keys(missing_template_fields(RESTAURANT, FAKE_PROFILE, {"day": "Friday"})) == [
        "party_size",
        "time",
    ]
    answers = {"party_size": "6", "day": "Friday", "time": "7pm"}
    assert missing_template_fields(RESTAURANT, FAKE_PROFILE, answers) == []


def test_a_blank_answer_is_missing() -> None:
    assert "party_size" in keys(
        missing_template_fields(RESTAURANT, FAKE_PROFILE, {"party_size": " "})
    )


def test_an_empty_profile_asks_for_everything() -> None:
    assert len(missing_template_fields(DOCTOR, [], {})) == len(DOCTOR.required_fields)


def test_known_profile_fields() -> None:
    assert known_profile_fields(RESTAURANT, FAKE_PROFILE) == ["calling_name", "phone"]
    assert known_profile_fields(DOCTOR, FAKE_PROFILE) == ["calling_name", "date_of_birth"]
    assert known_profile_fields(DOCTOR, []) == []


def test_default_sharing_on5() -> None:
    assert default_sharing("date_of_birth") == "ask_first"
    assert default_sharing("address") == "ask_first"
    assert default_sharing("insurance_member_id") == "ask_first"
    assert default_sharing("phone") == "share_freely"
    assert default_sharing("calling_name") == "share_freely"


def test_with_profile_field_adds_with_default_sharing() -> None:
    assert with_profile_field([], "date_of_birth", "2004-03-15") == [
        ProfileField("date_of_birth", "2004-03-15", "ask_first")
    ]


def test_with_profile_field_replaces_in_place() -> None:
    updated = with_profile_field(FAKE_PROFILE, "phone", "+16175550199")
    assert keys(updated) == keys(FAKE_PROFILE)
    assert next(f for f in updated if f.key == "phone").value == "+16175550199"


def test_with_profile_field_never_loosens_ask_first() -> None:
    """Server-only guard: re-typing an Ask-me-first field can't make it Share freely."""
    profile = [ProfileField("email", "alex@example.test", "ask_first")]
    assert with_profile_field(profile, "email", "alex.c@example.test")[0].sharing == "ask_first"


# ---------------------------------------------------------------- brief.test.ts


def test_restaurant_full_chat_lands_every_list_in_brief_v1_shape() -> None:
    d = draft(
        template_key="restaurant_booking",
        goal="Restaurant: book a table",
        business_name="Corner Table Bistro",
        phone_e164="+16175550100",
        answers={"party_size": "6", "day": "Friday", "time": "7pm"},
        constraints=["Between 6:30pm and 8pm"],
        preferences=["Window seat"],
        fallbacks=["Saturday same time", "Friday at 8:30pm"],
        never_agree=["A deposit or fee"],
    )
    built = build_brief(d, RESTAURANT, FAKE_PROFILE)
    expect_valid_brief(built.brief, FAKE_PROFILE)
    assert built.missing_fields == []
    brief = built.brief
    assert brief["constraints"] == [{"text": "Between 6:30pm and 8pm"}]
    assert brief["preferences"] == [{"text": "Window seat"}]
    assert brief["fallbacks"] == [{"text": "Saturday same time"}, {"text": "Friday at 8:30pm"}]
    assert brief["never_agree"] == [{"text": "A deposit or fee"}]
    assert brief["task_line"] == RESTAURANT.task_line
    assert brief["task_vars"] == {
        "party_size": {"literal": "6"},
        "day": {"literal": "Friday"},
        "time": {"literal": "7pm"},
    }
    assert brief["share_freely_keys"] == ["calling_name", "phone"]
    assert brief["ask_first_keys"] == []


def test_doctor_dob_is_ask_first_and_never_in_the_brief() -> None:
    d = draft(
        template_key="doctor_booking",
        goal="Doctor: book an appointment",
        business_name="Back Bay Family Clinic",
        phone_e164="+16175550111",
        answers={"provider_name": "Dr. Lee", "patient_status": "Existing", "reason": "Check-up"},
        constraints=["Before 3pm"],
        fallbacks=["Friday morning"],
    )
    built = build_brief(d, DOCTOR, FAKE_PROFILE)
    expect_valid_brief(built.brief, FAKE_PROFILE)
    assert built.missing_fields == []
    assert built.brief["task_vars"] == {"provider_name": {"literal": "Dr. Lee"}}
    assert built.brief["share_freely_keys"] == ["calling_name", "phone"]
    assert built.brief["ask_first_keys"] == ["date_of_birth"]
    assert built.brief["never_agree"] == []


def test_custom_call_uses_the_tech_doc_task_line() -> None:
    d = draft(fallbacks=["Any cake they have ready"])
    built = build_brief(d, None, FAKE_PROFILE)
    expect_valid_brief(built.brief, FAKE_PROFILE)
    assert built.missing_fields == []
    assert built.brief["template_key"] is None
    assert built.brief["task_line"] == CUSTOM_TASK_LINE
    assert built.brief["task_vars"] == {
        "goal_summary": {"literal": "ask if they can hold a cake for pickup tomorrow"}
    }
    assert built.brief["fallbacks"] == [{"text": "Any cake they have ready"}]


def test_custom_call_ignores_template_answers_and_strips_trailing_punctuation() -> None:
    built = build_brief(draft(goal="  Check opening hours?!", answers={"day": "Friday"}), None, [])
    assert built.brief["task_vars"] == {"goal_summary": {"literal": "check opening hours"}}


def test_a_goal_of_only_punctuation_leaves_goal_summary_missing() -> None:
    assert "goal_summary" in build_brief(draft(goal="?!"), None, FAKE_PROFILE).missing_fields


def test_an_empty_fallbacks_list_is_never_missing() -> None:
    built = build_brief(draft(fallbacks=[]), None, FAKE_PROFILE)
    assert built.brief["fallbacks"] == []
    assert built.missing_fields == []


def test_unanswered_template_details_are_missing_and_left_out_of_task_vars() -> None:
    d = draft(template_key="restaurant_booking", goal="Book a table", answers={"party_size": "4"})
    built = build_brief(d, RESTAURANT, FAKE_PROFILE)
    assert built.missing_fields == ["day", "time"]
    assert built.brief["task_vars"] == {"party_size": {"literal": "4"}}


def test_a_share_freely_profile_field_is_a_field_ref_never_its_value() -> None:
    t = RESTAURANT.model_copy(update={"task_line": "Book a table under {calling_name}."})
    built = build_brief(draft(template_key=t.key, goal="Book"), t, FAKE_PROFILE)
    assert built.brief["task_vars"] == {"calling_name": {"field_ref": "calling_name"}}
    assert '"Alex"' not in json.dumps(built.brief)


def test_an_ask_first_placeholder_is_reported_missing_never_referenced() -> None:
    # The template rules (R5) refuse this template; the builder must still be safe.
    t = DOCTOR.model_copy(update={"task_line": "Patient born {date_of_birth}."})
    built = build_brief(draft(template_key=t.key, goal="Book"), t, FAKE_PROFILE)
    assert built.brief["task_vars"] == {}
    assert "date_of_birth" in built.missing_fields


def test_a_missing_calling_name_is_reported() -> None:
    assert "calling_name" in build_brief(draft(), None, without("calling_name")).missing_fields


def test_dob_added_in_the_chat_goes_to_ask_first_keys_never_into_the_json() -> None:
    d = draft(
        template_key="doctor_booking",
        goal="Book",
        answers={"provider_name": "Dr. Lee", "patient_status": "New", "reason": "Check-up"},
        profile_additions=[("date_of_birth", "2004-03-15")],
    )
    built = build_brief(d, DOCTOR, without("date_of_birth"))
    assert built.missing_fields == []
    assert built.brief["ask_first_keys"] == ["date_of_birth"]
    assert "2004-03-15" not in json.dumps(built.brief)


def test_missing_fields_have_no_repeats() -> None:
    built = build_brief(draft(template_key="restaurant_booking", goal="Book"), RESTAURANT, [])
    assert built.missing_fields == ["party_size", "day", "time", "calling_name", "phone"]


def test_list_items_are_fresh_objects() -> None:
    d = draft(constraints=["Before 6pm"])
    first = build_brief(d, None, FAKE_PROFILE).brief
    first["constraints"][0]["text"] = "changed"
    assert build_brief(d, None, FAKE_PROFILE).brief["constraints"] == [{"text": "Before 6pm"}]


def test_every_template_builds_ready_with_all_answers() -> None:
    for t in TEMPLATES:
        answers = {f.key: "Example" for f in t.required_fields if f.source == "call"}
        built = build_brief(
            draft(template_key=t.key, goal=t.label, answers=answers), t, FAKE_PROFILE
        )
        expect_valid_brief(built.brief, FAKE_PROFILE)
        assert built.missing_fields == [], t.key


# ---------------------------------------------------------------- build_response (readiness)


def test_build_response_gives_the_opening_line_when_ready() -> None:
    d = draft(
        template_key="restaurant_booking",
        goal="Restaurant: book a table",
        answers={"party_size": "6", "day": "Friday", "time": "7pm"},
    )
    result = build_response(d, FAKE_PROFILE)
    assert result["missing_fields"] == []
    assert result["opening_line"] == (
        "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. "
        "I'd like to book a table for 6 on Friday at 7pm."
    )


def test_build_response_has_no_opening_line_while_something_is_missing() -> None:
    result = build_response(draft(template_key="doctor_booking", goal="Book"), FAKE_PROFILE)
    assert result["missing_fields"] == ["provider_name", "patient_status", "reason"]
    assert result["opening_line"] is None


def test_build_response_catches_an_ask_first_value_typed_into_the_brief() -> None:
    result = build_response(draft(constraints=["Born 2004-03-15, say if asked"]), FAKE_PROFILE)
    assert result["missing_fields"] == ["date_of_birth"]
    assert result["opening_line"] is None


def test_build_response_counts_a_calling_name_added_in_the_chat() -> None:
    d = draft(profile_additions=[("calling_name", "Sam")])
    result = build_response(d, without("calling_name"))
    assert result["missing_fields"] == []
    assert "on behalf of Sam." in result["opening_line"]
    assert "Sam" not in json.dumps(result["brief"])


# ---------------------------------------------------------------- HTTP, without a database


@pytest.fixture
def client() -> TestClient:
    settings = Settings(supabase_jwt_secret=SECRET, supabase_jwt_issuer=ISSUER)  # type: ignore[arg-type]
    return TestClient(create_app(settings))


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "template_key": "restaurant_booking",
        "goal": "Restaurant: book a table",
        "business_name": "Corner Table Bistro",
        "phone_e164": "(617) 555-0100",
        "answers": {"party_size": "6", "day": "Friday", "time": "7pm"},
        "profile_additions": [],
        "constraints": [{"text": "Between 6:30pm and 8pm"}],
        "preferences": [],
        "fallbacks": [],
        "never_agree": [],
    }
    data.update(overrides)
    return data


def auth() -> dict[str, str]:
    return bearer(make_token())


def test_builder_routes_need_auth(client: TestClient) -> None:
    for method, url in [("GET", "/briefs/templates"), ("POST", "/briefs/build")]:
        response = client.request(method, url, json=body())
        assert response.status_code == 401, (method, url)
        assert response.json()["error"]["code"] == "unauthenticated"


def test_templates_route_lists_the_templates(client: TestClient) -> None:
    """Also proves /briefs/templates isn't swallowed by GET /briefs/{brief_id}."""
    response = client.get("/briefs/templates", headers=auth())
    assert response.status_code == 200
    assert response.json() == {"templates": templates_json()}


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"goal": ""}, "invalid_request"),
        ({"goal": "x" * 301}, "invalid_request"),
        ({"business_name": ""}, "invalid_request"),
        ({"answers": {"Party Size": "6"}}, "invalid_request"),
        ({"answers": {"party_size": "x" * 201}}, "invalid_request"),
        ({"constraints": [{"text": ""}]}, "invalid_request"),
        ({"constraints": [{"text": str(n)} for n in range(6)]}, "invalid_request"),
        (
            {
                "profile_additions": [
                    {"key": "date_of_birth", "value": "x", "sharing": "share_freely"}
                ]
            },
            "invalid_request",
        ),
        ({"surprise": 1}, "invalid_request"),
        ({"template_key": "restaurant_book"}, "unknown_template"),
        ({"phone_e164": "416 555 0123"}, "invalid_phone"),
        ({"phone_e164": "911"}, "invalid_phone"),
    ],
)
def test_build_rejects_a_bad_body(client: TestClient, overrides: dict[str, Any], code: str) -> None:
    response = client.post("/briefs/build", json=body(**overrides), headers=auth())
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code


def test_build_needs_template_key_even_when_null(client: TestClient) -> None:
    data = body()
    del data["template_key"]
    response = client.post("/briefs/build", json=data, headers=auth())
    assert response.status_code == 422


def test_a_valid_build_reaches_the_database_step(client: TestClient) -> None:
    """No Idempotency-Key needed; without DATABASE_URL a valid body gets the shared 503."""
    for data in (body(), body(template_key=None, goal="Ask about gluten-free cakes", answers={})):
        response = client.post("/briefs/build", json=data, headers=auth())
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "database_unavailable"

"""Reading structured rules out of a brief's typed items (``app/briefs/rules.py``). Fake data.

The reported cake call: the user wrote "need to pick up by tomorrow 10am" in the goal and
"i cna too 11 am pick up too" as a fallback, neither had rules, so an offer of 12 PM was taken as
a fallback. No database, no network.
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.briefs.router import build_response, read_rules, with_rules
from app.briefs.rules import Answer, add_rules
from app.calls.gates import REFUSED_NOT_A_FALLBACK, CallFacts, low_offer_gate
from app.calls.scoring import score_offer
from app.contracts import validator_for
from tests.test_briefs_builder import FAKE_PROFILE, draft

TUESDAY = date(2026, 11, 17)
NOV_24 = date(2026, 11, 24)
EVAL_CASES = Path(__file__).resolve().parent.parent / "evals" / "incall" / "cases.json"


def brief_of(
    *,
    goal: str = "x",
    constraints: list[str] | None = None,
    preferences: list[str] | None = None,
    fallbacks: list[str] | None = None,
) -> dict[str, Any]:
    def items(texts: list[str] | None) -> list[dict[str, str]]:
        return [{"text": t} for t in texts or []]

    return {
        "goal": goal,
        "constraints": items(constraints),
        "preferences": items(preferences),
        "fallbacks": items(fallbacks),
    }


def rules_for(text: str, today: date = TUESDAY) -> list[dict[str, Any]] | None:
    """The rules read from one typed constraint."""
    return add_rules(brief_of(constraints=[text]), today).brief["constraints"][0].get("rules")


def time_rule(**bounds: str) -> list[dict[str, Any]]:
    return [{"kind": "time", **bounds}]


# --- times ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Before 3pm", {"latest": "14:59"}),
        ("before noon", {"latest": "11:59"}),
        ("by 10am", {"latest": "10:00"}),
        ("no later than noon", {"latest": "12:00"}),
        ("until 6:30 pm", {"latest": "18:30"}),
        ("up to 5pm", {"latest": "17:00"}),
        ("before 19:30", {"latest": "19:29"}),
        ("8pm or earlier", {"latest": "20:00"}),
        ("after 6pm", {"earliest": "18:01"}),
        ("from 6pm", {"earliest": "18:00"}),
        ("no earlier than 5:15 p.m.", {"earliest": "17:15"}),
        ("6pm or later", {"earliest": "18:00"}),
        ("at or after 5pm", {"earliest": "17:00"}),
        ("Between 6pm and 8pm", {"earliest": "18:00", "latest": "20:00"}),
        ("between 6:30pm and 8pm", {"earliest": "18:30", "latest": "20:00"}),
        ("between 6 and 8pm", {"earliest": "18:00", "latest": "20:00"}),
        ("6-8pm", {"earliest": "18:00", "latest": "20:00"}),
        ("between 11 and 1pm", {"earliest": "11:00", "latest": "13:00"}),
        ("morning", {"latest": "11:59"}),
        ("in the afternoon", {"earliest": "12:00", "latest": "17:59"}),
    ],
)
def test_a_typed_time_becomes_a_time_rule(text: str, expected: dict[str, str]) -> None:
    assert rules_for(text) == time_rule(**expected)


@pytest.mark.parametrize("text", ["7:30", "before 3", "6-8", "after 5", "between 6 and 8"])
def test_a_time_that_could_be_either_half_of_the_day_is_not_guessed(text: str) -> None:
    parsed = add_rules(brief_of(constraints=[text]), TUESDAY)
    assert parsed.brief["constraints"][0].get("rules") is None
    assert parsed.clarifications or text == "6-8"  # "6-8" has no time word at all


def test_a_missing_am_or_pm_is_asked_about() -> None:
    parsed = add_rules(brief_of(constraints=["before 3"]), TUESDAY)
    [ask] = parsed.clarifications
    assert "morning or in the evening" in ask.question
    assert ask.item == "before 3"


@pytest.mark.parametrize(
    "text", ["table for 6", "10 people", "5 amazing cakes", "room 12", "baby cake", "by friday"]
)
def test_words_and_numbers_that_are_not_times_make_no_time_rule(text: str) -> None:
    rules = rules_for(text) or []
    assert not [r for r in rules if r["kind"] == "time"]


def test_by_inside_another_word_is_not_a_deadline() -> None:
    parsed = add_rules(brief_of(constraints=["baby shower cake 3pm"]), TUESDAY)
    assert parsed.brief["constraints"][0].get("rules") is None  # "baby 3pm" is not "by 3pm"
    assert parsed.clarifications  # a lone 3pm is asked about


def test_a_window_that_contradicts_itself_makes_no_rule() -> None:
    assert rules_for("after 8pm and before 6pm") is None


# --- days -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("tomorrow", "2026-11-18"),
        ("today", "2026-11-17"),
        ("Friday", "2026-11-20"),
        ("Tuesday", "2026-11-24"),  # today's weekday means the next one
        ("On Thursday 19 November", "2026-11-19"),
        ("Dec 23", "2026-12-23"),
        ("Friday 20 November", "2026-11-20"),  # the date wins over the weekday
    ],
)
def test_a_typed_day_becomes_a_date_rule(text: str, day: str) -> None:
    assert rules_for(text) == [{"kind": "date", "on": day}]


@pytest.mark.parametrize("text", ["next Friday", "today or tomorrow", "Friday or Saturday"])
def test_a_day_that_could_mean_two_dates_is_not_guessed(text: str) -> None:
    assert rules_for(text) is None


# --- party size and person ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Party of 6", {"exactly": 6}),
        ("table for 4", {"exactly": 4}),
        ("6 people", {"exactly": 6}),
        ("at least 4", {"min": 4}),
        ("4 or more", {"min": 4}),
        ("up to 6", {"max": 6}),
        ("no more than 8 guests", {"max": 8}),
        ("at least 4, up to 6", {"min": 4, "max": 6}),
    ],
)
def test_a_typed_party_size_becomes_a_party_size_rule(text: str, expected: dict[str, int]) -> None:
    assert rules_for(text) == [{"kind": "party_size", **expected}]


def test_a_party_size_out_of_range_or_doubled_is_not_read() -> None:
    assert rules_for("party of 99") is None
    assert rules_for("party of 4 or party of 6") is None


def test_with_a_titled_person_becomes_a_person_rule() -> None:
    assert rules_for("With Dr. Rivera") == [{"kind": "person", "name": "Dr. Rivera"}]
    assert rules_for("with Dr. Anna Rivera on Thursday") == [
        {"kind": "date", "on": "2026-11-19"},
        {"kind": "person", "name": "Dr. Anna Rivera"},
    ]
    assert rules_for("with Marco") is None  # no title: could be anyone


# --- the same rules the eval's hand-written briefs use ---------------------------------------


def test_the_parser_reads_the_evals_hand_written_rules() -> None:
    data = json.loads(EVAL_CASES.read_text(encoding="utf-8"))
    agreed = 0
    for name in ("A", "B"):
        spec = data["briefs"][name]
        parsed = add_rules(
            brief_of(
                goal=spec["goal"],
                constraints=spec["constraints"],
                preferences=spec["preferences"],
                fallbacks=spec["fallbacks"],
            ),
            TUESDAY,
        )
        for key in ("constraints", "preferences", "fallbacks"):
            for item in parsed.brief[key]:
                hand = spec["rules"].get(item["text"])
                if hand is None:
                    assert item.get("rules") is None, item["text"]
                elif item["text"] not in SAME_TIME_OR_EXACT:
                    assert item["rules"] == hand, item["text"]
                    agreed += 1
    assert agreed == 6  # six items have hand rules and match; "Window seat" stays text only


# Where the hand rules copy a window or a party size from an earlier constraint ("same time"),
# or pin a bare time to exactly that time, the parser gives less, never something wrong.
SAME_TIME_OR_EXACT = {"Sunday 22 November, same time", "Saturday 21 November, 6pm"}


def test_where_the_parser_gives_less_than_the_hand_rules_it_gives_a_subset() -> None:
    data = json.loads(EVAL_CASES.read_text(encoding="utf-8"))
    spec = data["briefs"]["B"]
    parsed = add_rules(
        brief_of(
            goal=spec["goal"],
            constraints=spec["constraints"],
            fallbacks=spec["fallbacks"],
        ),
        TUESDAY,
    )
    same_time, six_pm = parsed.brief["fallbacks"][0], parsed.brief["fallbacks"][1]
    assert same_time["rules"] == [{"kind": "date", "on": "2026-11-22"}]
    assert six_pm["rules"] == [{"kind": "date", "on": "2026-11-21"}]
    [ask] = parsed.clarifications  # the bare 6pm is asked about, not guessed
    assert "Saturday 21 November, 6pm" in ask.question


# --- the reported cake call -----------------------------------------------------------------

CAKE = brief_of(
    goal="i wanna order a cake for my friend, need to pick up by tomorrow 10am",
    constraints=["include happy birthday cake topper, candles, knife", "need to have ice pack"],
    preferences=["cake has mochi"],
    fallbacks=[
        "i cna too 11 am pick up too",
        "is there another branch for pick up if this store is not available?",
        "tea flavoured cake if possible",
    ],
)


def test_a_deadline_in_the_goal_becomes_a_constraint_in_the_users_words() -> None:
    parsed = add_rules(CAKE, NOV_24).brief
    assert [c["text"] for c in parsed["constraints"]] == [
        "include happy birthday cake topper, candles, knife",
        "need to have ice pack",
        "By tomorrow 10am",
    ]
    assert parsed["constraints"][2]["rules"] == [
        {"kind": "date", "on": "2026-11-25"},
        {"kind": "time", "latest": "10:00"},
    ]
    assert "rules" not in parsed["constraints"][0]  # text only: nothing to compare


def test_a_later_pickup_time_in_a_fallback_is_a_later_latest() -> None:
    fallbacks = add_rules(CAKE, NOV_24).brief["fallbacks"]
    assert fallbacks[0]["rules"] == time_rule(latest="11:00")
    assert "rules" not in fallbacks[1] and "rules" not in fallbacks[2]  # words only


def test_the_cake_briefs_rules_fail_12pm_against_the_deadline_and_the_11am_fallback() -> None:
    brief = add_rules(CAKE, NOV_24).brief
    noon = score_offer(brief, {"time": "12:00"})
    assert noon.fits is False  # tomorrow by 10:00
    assert [f.check for f in noon.fallbacks][0] == "fail"  # the 11am fallback
    # 11:00 tomorrow is the fallback; 9:30 tomorrow meets the deadline itself.
    eleven = score_offer(brief, {"date": "2026-11-25", "time": "11:00"})
    assert eleven.matches_fallback is True
    early = score_offer(brief, {"date": "2026-11-25", "time": "09:30"})
    assert early.fits is None  # the text-only constraints are for the model to judge
    assert [c.check for c in early.constraints][2] == "pass"


def test_text_only_fallbacks_still_leave_room_for_the_model_to_say_one_matches() -> None:
    """A known gap. Python can say the 11am fallback fails 12:00, but "tea flavoured cake" and
    "another branch" are words only, so it can't say that *no* fallback matches. The model's
    own claim then stands and a take_fallback at 12:00 is not refused."""
    brief = add_rules(CAKE, NOV_24).brief
    noon = score_offer(brief, {"time": "12:00"})
    assert noon.matches_fallback is None
    dims = {"meets_constraints": True, "matches_fallback": True}
    assert low_offer_gate("take_fallback", dims, CallFacts(), noon) is None


def test_with_only_readable_fallbacks_the_12pm_take_fallback_is_refused() -> None:
    readable = brief_of(
        goal=CAKE["goal"],
        constraints=["need to have ice pack"],
        fallbacks=["i cna too 11 am pick up too"],
    )
    brief = add_rules(readable, NOV_24).brief
    noon = score_offer(brief, {"time": "12:00"})
    assert noon.matches_fallback is False
    dims = {"meets_constraints": True, "matches_fallback": True}  # the model's own claim
    assert low_offer_gate("take_fallback", dims, CallFacts(), noon) == REFUSED_NOT_A_FALLBACK


def test_the_deadline_can_follow_the_time_and_the_day_can_follow_it() -> None:
    parsed = add_rules(brief_of(goal="cake please, ready by 10am tomorrow."), NOV_24).brief
    [item] = parsed["constraints"]
    assert item["text"] == "By 10am tomorrow"
    assert item["rules"] == [
        {"kind": "date", "on": "2026-11-25"},
        {"kind": "time", "latest": "10:00"},
    ]


def test_a_deadline_the_user_already_typed_is_not_added_twice() -> None:
    typed = brief_of(
        goal="need to pick up by tomorrow 10am", constraints=["Pickup by tomorrow 10am"]
    )
    parsed = add_rules(typed, NOV_24).brief
    assert [c["text"] for c in parsed["constraints"]] == ["Pickup by tomorrow 10am"]
    again = add_rules(brief_of(goal=typed["goal"]), NOV_24).brief
    assert add_rules(again, NOV_24).brief == again  # saving the result again changes nothing


def test_a_goal_without_a_clear_deadline_adds_nothing() -> None:
    for goal in ("cake for pickup tomorrow", "by friday", "before the weekend", "order a cake"):
        assert add_rules(brief_of(goal=goal), NOV_24).brief["constraints"] == []
    assert add_rules(brief_of(goal="back by 3"), NOV_24).brief["constraints"] == []  # no am/pm


def test_a_full_constraint_list_is_left_alone() -> None:
    full = brief_of(goal="by 10am", constraints=[f"item {i}" for i in range(5)])
    assert len(add_rules(full, NOV_24).brief["constraints"]) == 5


# --- when the parser is not sure it asks ----------------------------------------------------


def test_a_lone_time_in_a_must_have_is_asked_about() -> None:
    parsed = add_rules(brief_of(constraints=["at 7pm"]), TUESDAY)
    assert parsed.brief["constraints"][0].get("rules") is None
    [ask] = parsed.clarifications
    assert ask.question == (
        'You wrote "at 7pm". Is 7pm the latest you would accept, or the only time that works?'
    )
    assert ask.list == "constraints"
    assert ask.answers == (
        Answer("7pm or earlier", rules=time_rule(latest="19:00")),
        Answer("7pm or later", rules=time_rule(earliest="19:00")),
        Answer("Only 7pm", rules=time_rule(earliest="19:00", latest="19:00")),
    )


def test_a_lone_time_in_a_fallback_earlier_than_the_deadline_is_asked_about() -> None:
    parsed = add_rules(brief_of(constraints=["by 10am"], fallbacks=["9am works too"]), TUESDAY)
    assert parsed.brief["fallbacks"][0].get("rules") is None
    assert len(parsed.clarifications) == 1


def test_a_lone_time_next_to_a_window_is_asked_about_not_read_as_a_later_latest() -> None:
    brief = brief_of(constraints=["Between 6pm and 8pm"], fallbacks=["Friday 8:30pm"])
    parsed = add_rules(brief, TUESDAY)
    assert parsed.brief["fallbacks"][0]["rules"] == [{"kind": "date", "on": "2026-11-20"}]
    assert len(parsed.clarifications) == 1


# --- how it treats a brief ------------------------------------------------------------------


def test_rules_already_on_an_item_are_kept() -> None:
    brief = brief_of(constraints=["Before 3pm"])
    brief["constraints"][0]["rules"] = time_rule(latest="12:00")  # the user's edit wins
    assert add_rules(brief, TUESDAY).brief["constraints"][0]["rules"] == time_rule(latest="12:00")


def test_an_odd_brief_does_not_raise() -> None:
    parsed = add_rules({"goal": "x"}, TUESDAY).brief
    assert parsed["constraints"] == [] and parsed["fallbacks"] == []
    odd = {"goal": 5, "constraints": ["Before 3pm", None, {"text": 7}], "preferences": "oops"}
    add_rules(odd, TUESDAY)  # must not raise


def test_the_input_brief_is_not_changed() -> None:
    original = json.loads(json.dumps(CAKE))
    add_rules(CAKE, NOV_24)
    assert CAKE == original


# --- the save path --------------------------------------------------------------------------


def contract_brief(**overrides: Any) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "goal": "Order a cake, pick up by tomorrow 10am",
        "business_name": "Coco Bakery",
        "phone_e164": "+16175550104",
        "template_key": None,
        "task_line": "I'm calling to order a cake.",
        "task_vars": {},
        "constraints": [{"text": "Needs an ice pack"}],
        "preferences": [],
        "fallbacks": [{"text": "11 am pickup too"}],
        "never_agree": [{"text": "Cake with nuts"}],
        "share_freely_keys": [],
        "ask_first_keys": [],
    }
    brief.update(overrides)
    return brief


def test_a_saved_brief_gets_its_rules_and_still_meets_the_contract() -> None:
    saved = with_rules(contract_brief())
    assert validator_for("brief.v1.json").is_valid(saved)
    assert saved["constraints"][-1]["text"] == "By tomorrow 10am"
    assert saved["fallbacks"][0]["rules"] == time_rule(latest="11:00")
    assert with_rules(saved) == saved  # saving it again changes nothing


def test_a_brief_whose_rules_would_break_the_contract_is_saved_without_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        brief = {"goal": ""}  # not a brief

    # ``app.briefs.router`` the module, not the router object the package exports under that name.
    monkeypatch.setattr(sys.modules["app.briefs.router"], "add_rules", lambda b, t: Broken())
    original = contract_brief()
    assert with_rules(original) == original


# --- the questions the API sends ------------------------------------------------------------


def questions_for(**lists: list[str]) -> list[Any]:
    return add_rules(brief_of(**lists), TUESDAY).clarifications


def test_a_question_says_which_list_its_item_is_in() -> None:
    [pref] = questions_for(preferences=["at 7pm"])
    [fallback] = questions_for(fallbacks=["at 7pm"])
    [constraint] = questions_for(constraints=["at 7pm"])
    assert (constraint.list, pref.list, fallback.list) == (
        "constraints",
        "preferences",
        "fallbacks",
    )


def test_a_question_becomes_the_json_the_chat_reads() -> None:
    [ask] = questions_for(constraints=["at 7pm"])
    assert ask.to_json() == {
        "item": "at 7pm",
        "list": "constraints",
        "question": (
            'You wrote "at 7pm". Is 7pm the latest you would accept, or the only time that works?'
        ),
        "answers": [
            {"label": "7pm or earlier", "rules": time_rule(latest="19:00"), "text": None},
            {"label": "7pm or later", "rules": time_rule(earliest="19:00"), "text": None},
            {
                "label": "Only 7pm",
                "rules": time_rule(earliest="19:00", latest="19:00"),
                "text": None,
            },
        ],
    }
    json.dumps(ask.to_json())  # plain JSON types


def test_a_missing_am_or_pm_is_answered_by_rewriting_the_item() -> None:
    [ask] = questions_for(constraints=["before 3"])
    assert ask.answers == (
        Answer("Morning (am)", text="before 3am"),
        Answer("Afternoon or evening (pm)", text="before 3pm"),
    )
    [vague] = questions_for(constraints=["between 6 and 8"])
    assert [a.text for a in vague.answers] == ["between 6 and 8am", "between 6 and 8pm"]
    [half] = questions_for(fallbacks=["7:30 works too"])
    assert [a.text for a in half.answers] == ["7:30am works too", "7:30pm works too"]


def test_a_rewritten_item_is_read_on_the_next_save() -> None:
    assert rules_for("before 3pm") == time_rule(latest="14:59")
    assert rules_for("before 3am") == time_rule(latest="02:59")
    assert rules_for("between 6 and 8pm") == time_rule(earliest="18:00", latest="20:00")
    again = add_rules(brief_of(constraints=["7:30pm"]), TUESDAY)
    assert [c.question for c in again.clarifications][0].startswith('You wrote "7:30pm". Is 7:30pm')


def test_an_item_the_user_decided_is_not_asked_about_again() -> None:
    brief = brief_of(constraints=["at 7pm"])
    brief["constraints"][0]["rules"] = []  # "decide on the call"
    parsed = add_rules(brief, TUESDAY)
    assert parsed.clarifications == []
    assert parsed.brief["constraints"][0]["rules"] == []
    settled = brief_of(constraints=["at 7pm"])
    settled["constraints"][0]["rules"] = time_rule(latest="19:00")
    assert add_rules(settled, TUESDAY).clarifications == []


def test_answering_every_question_leaves_nothing_to_ask() -> None:
    brief = brief_of(
        goal="cake by tomorrow 10am", constraints=["at 7pm"], fallbacks=["9am works too"]
    )
    first = add_rules(brief, NOV_24)
    assert len(first.clarifications) == 2
    for ask in first.clarifications:  # the user taps the first answer, or "decide on the call"
        item = next(i for i in brief[ask.list] if i["text"] == ask.item)
        item["rules"] = ask.answers[0].rules if ask.answers[0].rules else []
    assert add_rules(brief, NOV_24).clarifications == []


def test_the_build_answer_carries_the_questions() -> None:
    built = build_response(draft(constraints=["at 7pm"], goal_summary="order a cake"), FAKE_PROFILE)
    [ask] = built["clarifications"]
    assert ask["item"] == "at 7pm" and ask["list"] == "constraints"
    assert [a["label"] for a in ask["answers"]] == ["7pm or earlier", "7pm or later", "Only 7pm"]
    assert build_response(draft(), FAKE_PROFILE)["clarifications"] == []


def test_a_brief_whose_rules_break_the_contract_is_saved_without_them_and_without_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        brief = {"goal": ""}
        clarifications = ["not asked"]

    monkeypatch.setattr(sys.modules["app.briefs.router"], "add_rules", lambda b, t: Broken())
    original = contract_brief()
    parsed = read_rules(original)
    assert parsed.brief == original and parsed.clarifications == []

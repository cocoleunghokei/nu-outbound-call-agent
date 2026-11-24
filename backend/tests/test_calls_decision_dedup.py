"""A share the agent repeats is one decision in the user's feed (``decision_dedup_key``)."""

from app.calls.decisions import decision_dedup_key


def test_a_share_of_the_same_field_has_one_key_per_call() -> None:
    first = {"action": "share_field", "field_key": "phone"}
    again = {"action": "share_field", "field_key": "phone", "reason": "Repeating it slowly."}
    assert decision_dedup_key(first, "tc-1") == decision_dedup_key(again, "tc-2")
    assert decision_dedup_key(first, "tc-1") == "decision:share_field:phone"


def test_shares_of_different_fields_are_different_decisions() -> None:
    phone = {"action": "share_field", "field_key": "phone"}
    email = {"action": "share_field", "field_key": "email"}
    assert decision_dedup_key(phone, "tc-1") != decision_dedup_key(email, "tc-2")


def test_every_other_decision_keeps_one_row_per_tool_call() -> None:
    accept = {"action": "accept", "field_key": "phone"}
    assert decision_dedup_key(accept, "tc-1") == "decision:tc-1"
    assert decision_dedup_key(accept, "tc-2") == "decision:tc-2"
    assert decision_dedup_key({"action": "counter"}, "tc-3") == "decision:tc-3"


def test_a_share_with_no_field_cannot_be_matched_to_an_earlier_one() -> None:
    nameless = {"action": "share_field"}
    assert decision_dedup_key(nameless, "tc-1") == "decision:tc-1"
    assert decision_dedup_key(nameless, "tc-2") == "decision:tc-2"
    assert decision_dedup_key({"action": "share_field", "field_key": ""}, "tc-3") == "decision:tc-3"

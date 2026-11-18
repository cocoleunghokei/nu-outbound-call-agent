"""Deterministic gates on the agent's actions (DC-4, DC-9, DC-13). Code, not prompt.

The agent's words go straight to the voice, so only the prompt can shape them. Its actions
arrive as tool calls, and these gates check them before they count. A refused action is not
recorded; the agent gets a tool result saying what to do instead, and corrects itself in
the same turn. Found by the in-call eval (``backend/evals/incall/notes/``):

- **Money** (``money_gate``): ``report_decision`` accept or take_fallback is refused when the
  business mentioned money (a deposit, a fee, a card hold, a charge) after the user's last
  "yes" to a ping. Read from the transcript, not from the model's own scores.
- **Low offer** (``low_offer_gate``): accept is refused when the model's own ``dimensions``
  say a hard constraint is broken and no fallback matches, unless the user's answer to the
  latest ping was a "yes". take_fallback is refused when neither check passes.
- **Too long** (``length_gate``): ``ask_user`` (a decision ping) and ``report_decision`` are
  refused when ``reason`` or ``missed_preference`` is over ``CARD_TEXT_MAX`` characters, the
  length the user can read on the card at a glance (DC-5). Models can't count characters, so
  the prompt alone kept missing it (D5-len in the in-call eval).
- **Give up** (``give_up_gate``): ``report_outcome`` with ``needs_follow_up`` is refused when
  the user was never asked (no ping answered or expired) and the user didn't press End. The
  agent must ask before it gives up.

The decisions are pure functions over a ``CallFacts`` snapshot, so they are unit-tested
without a database; ``load_facts`` reads that snapshot inside the ingress transaction.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.calls.store import is_ending

# The same words the in-call eval's G4 check counts as money (cases.json).
MONEY_WORDS = re.compile(
    r"\$\s*\d|\bdeposits?\b|\bfees?\b|\bcredit card\b|\bcard hold\b|\bcharges?\b"
    r"|\bpay(ment)?\b|\bprepa(y|id|yment)\b",
    re.IGNORECASE,
)
# A reply that approves: a one-tap "Yes, book it", "Share it", or free text starting with yes.
APPROVING_REPLY = re.compile(
    r"^\s*(yes|yeah|yep|ok|okay|sure|fine|go ahead|approved?|book it|share it|take it)\b",
    re.IGNORECASE,
)
SETTLING_ACTIONS = frozenset({"accept", "take_fallback"})
# DC-5: a reason the user reads in about 2 seconds. The schemas allow 200; this is the policy.
CARD_TEXT_MAX = 80
CARD_TEXT_FIELDS = ("reason", "missed_preference")

REFUSED_MONEY = (
    "Not done: the business mentioned money (a deposit, fee, card hold or charge) and the user "
    "has not approved it. Do not agree to it. Call ask_user about it first."
)
REFUSED_LOW_OFFER = (
    "Not done: your own checks say this offer breaks a hard constraint and matches no "
    "fallback, and the user has not approved it. Do not accept it. Counter once, or call "
    "ask_user."
)
REFUSED_GIVE_UP = (
    "Not done: you have not asked the user yet. Do not end the call. Call ask_user with what "
    "they offered and what the user asked for, then wait for the answer."
)
REFUSED_TOO_LONG = (
    f"Not done: reason and missed_preference must each be {CARD_TEXT_MAX} characters or fewer, "
    "so the user can read the card at a glance. Call the same tool again right away with "
    "shorter wording: one idea, plain words. Say nothing to the business about this."
)
REFUSALS = (REFUSED_MONEY, REFUSED_LOW_OFFER, REFUSED_GIVE_UP, REFUSED_TOO_LONG)


@dataclass(frozen=True)
class CallFacts:
    """What the gates need to know about a call so far.

    ``money_since_yes``: the business mentioned money after the user's last approving reply
    (or ever, if there was none). ``latest_ping_approved``: the most recent ping was answered
    with an approving reply. ``user_was_asked``: some ping was answered or expired.
    ``ending``: the user pressed End (or the pause cap fired).
    """

    money_since_yes: bool = False
    latest_ping_approved: bool = False
    user_was_asked: bool = False
    ending: bool = False


def mentions_money(text: str) -> bool:
    """True when ``text`` mentions a deposit, fee, card hold, charge or a dollar amount."""
    return MONEY_WORDS.search(text) is not None


def is_approving(reply_text: str) -> bool:
    """True when the user's reply to a ping approves it (yes, share it, book it, ...)."""
    return APPROVING_REPLY.search(reply_text) is not None


def money_gate(action: str, facts: CallFacts) -> str | None:
    """The refusal for taking an offer the user hasn't approved money for, else None."""
    if action in SETTLING_ACTIONS and facts.money_since_yes:
        return REFUSED_MONEY
    return None


def low_offer_gate(
    action: str, dimensions: Mapping[str, Any] | None, facts: CallFacts
) -> str | None:
    """The refusal for accepting an offer the model itself scored as breaking the rules."""
    if dimensions is None or facts.latest_ping_approved:
        return None
    meets = dimensions.get("meets_constraints")
    matches = dimensions.get("matches_fallback")
    if action == "accept" and meets is False and matches is not True:
        return REFUSED_LOW_OFFER
    if action == "take_fallback" and meets is False and matches is False:
        return REFUSED_LOW_OFFER
    return None


def length_gate(arguments: Mapping[str, Any]) -> str | None:
    """The refusal for a ``reason`` or ``missed_preference`` too long for the card, else None."""
    for key in CARD_TEXT_FIELDS:
        value = arguments.get(key)
        if isinstance(value, str) and len(value.strip()) > CARD_TEXT_MAX:
            return REFUSED_TOO_LONG
    return None


def give_up_gate(outcome: str, facts: CallFacts) -> str | None:
    """The refusal for ending as needs_follow_up without ever asking the user, else None."""
    if outcome == "needs_follow_up" and not facts.user_was_asked and not facts.ending:
        return REFUSED_GIVE_UP
    return None


def facts_from_timeline(
    timeline: list[tuple[str, str]], *, latest_ping_answer: str | None, ending: bool
) -> CallFacts:
    """``CallFacts`` from the call's events in order, as ``(kind, text)``.

    Kinds: ``business`` (a business transcript line), ``answered`` (a decision ping answered,
    with the reply text), ``field_answered`` (an Ask-me-first approval answered: it counts as
    asking, never as approving money) and ``expired`` (a ping that timed out).
    ``latest_ping_answer`` is the reply to the most recent ping if it was a decision ping and
    was answered, else None.
    """
    money_since_yes = False
    asked = False
    for kind, text in timeline:
        if kind == "business" and mentions_money(text):
            money_since_yes = True
        elif kind == "answered":
            asked = True
            if is_approving(text):
                money_since_yes = False
        elif kind in ("expired", "field_answered"):
            asked = True
    return CallFacts(
        money_since_yes=money_since_yes,
        latest_ping_approved=latest_ping_answer is not None and is_approving(latest_ping_answer),
        user_was_asked=asked,
        ending=ending,
    )


async def load_facts(conn: AsyncConnection[Any], call_id: UUID) -> CallFacts:
    """Read ``CallFacts`` for a call: its business transcript lines and resolved pings."""
    rows = await (
        await conn.execute(
            "select e.type, e.payload, p.kind from public.call_events e"
            " left join public.pings p on p.ping_id::text = e.payload->>'ping_id'"
            " where e.call_id = %s and (e.type = 'ping.resolved'"
            "  or (e.type = 'transcript.final' and e.payload->>'role' = 'business'))"
            " order by e.seq",
            (call_id,),
        )
    ).fetchall()
    timeline: list[tuple[str, str]] = []
    for event_type, payload, ping_kind in rows:
        if not isinstance(payload, dict):
            continue
        if event_type == "transcript.final":
            timeline.append(("business", str(payload.get("text") or "")))
        elif payload.get("state") == "answered":
            kind = "answered" if ping_kind == "decision" else "field_answered"
            timeline.append((kind, str(payload.get("reply_text") or "")))
        elif payload.get("state") == "expired":
            timeline.append(("expired", ""))
    latest = await (
        await conn.execute(
            "select p.state, p.kind, e.payload->>'reply_text' from public.pings p"
            " left join public.call_events e on e.call_id = p.call_id"
            "  and e.type = 'ping.resolved' and e.payload->>'ping_id' = p.ping_id::text"
            " where p.call_id = %s order by p.source_ts desc limit 1",
            (call_id,),
        )
    ).fetchone()
    answered = latest is not None and latest[0] == "answered" and latest[1] == "decision"
    answer = latest[2] if answered and latest is not None else None
    return facts_from_timeline(
        timeline, latest_ping_answer=answer, ending=await is_ending(conn, call_id)
    )

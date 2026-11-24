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
- **Scored offers** (``app/calls/scoring.py``): when the tool call carries the ``offer`` the
  model heard and the brief has structured ``rules``, Python's comparison replaces the
  model's own ``meets_constraints`` and ``matches_fallback`` in the Low-offer and
  needless-ping gates, and the refusal names the broken rule. Where Python can't tell
  (unknown), the model's ``dimensions`` decide as before.
- **The user's own instruction** (DC-10): the Low-offer and missing-detail gates step aside
  for an accept or take_fallback marked ``covered_by_instruction`` once the user has sent an
  instruction during the call ("4pm is fine too", R-09): the typed rules are older than that
  message, and Python can't read free text. Only with both: the flag alone, before any
  instruction, changes nothing. The money gate never steps aside for an instruction.
- **Needless counter** (``needless_counter_gate``): ``report_decision`` counter is refused
  when Python finds the offer meets every typed rule (a brief with none at all fits, Q8):
  there is nothing to counter, so the agent should accept and say what changed (TP-03 and
  TP-04 countered "Dr. Patel" and "8:30pm" because the opening line said otherwise). Never
  for money or an ask-first field, and never where Python can't tell.
- **Missing detail** (``missing_detail_gate``): accept or take_fallback is refused when the
  brief has a rule Python could check, but the offer the model wrote down leaves out the
  value it needs (C4-05: "take the Friday morning fallback" with no time, which the business
  never offered). A booking never rests on a guess. The user's "yes" lets it through.
- **Pinging a fit** (``needless_ping_gate``): ``ask_user`` with confidence high is refused
  when neither gate applies (no ask-first field, no money): the model's own score says it
  can act alone, so it should take the offer, not ask (C4-01).
- **Unsaid name** (``unsaid_name_gate``): ``report_outcome`` is refused when
  ``details.staff_name`` is a name the business never said (SM-06 put the restaurant's own
  name there). Dates, times and confirmation numbers are checked after the call instead
  (``app/postcall/decide.py``).
- **Wrong day** (``report_day_gate``): ``report_outcome`` is refused when ``details.date`` is not
  the day the user typed ("Dec 23", read into 2026-12-23 by ``requested_day``), not a day a date
  rule in the brief allows, and not a day the business named. The requirement list anchors the
  date, so a misheard transcript or a guessed month can't become the booking's date (a call
  reported 2026-10-23 for December 23).
- **Second counter** (``counter_gate``): ``report_decision`` counter is refused when the
  agent already countered and the user hasn't answered a ping since (Q9: counter once, then
  ask). C4-05 countered twice instead of pinging. After the user answers ("ask for Friday
  instead"), the agent may counter again.
- **Give up** (``give_up_gate``): ``report_outcome`` with ``needs_follow_up`` is refused when
  the user was never asked (no ping answered or expired) and the user didn't press End. The
  agent must ask before it gives up.

The decisions are pure functions over a ``CallFacts`` snapshot, so they are unit-tested
without a database; ``load_facts`` reads that snapshot inside the ingress transaction.
"""

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.calls.prompt import one_line
from app.calls.requested_day import business_named_day
from app.calls.scoring import OfferScore
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
REFUSED_NEEDLESS_PING = (
    "Not done: your own checks say this offer fits and needs no approval, so do not ask the "
    "user. Accept it yourself and call report_decision with action accept."
)
REFUSED_UNSAID_NAME = (
    "Not done: details.staff_name must be the name of the person you spoke with, exactly as "
    "they said it. They never said that name. Call report_outcome again without staff_name."
)
# Every refusal starts with this, including the ones that name a rule, so the eval can tell
# a refused call from an accepted one without knowing every wording (``is_refusal``).
REFUSAL_PREFIX = "Not done:"


def is_refusal(result: str) -> bool:
    """True when a tool result is a gate's refusal rather than the action taking effect."""
    return result.startswith(REFUSAL_PREFIX)


def breaks_rule_refusal(rule_text: str) -> str:
    """The refusal for accepting an offer Python found breaks ``rule_text``."""
    return (
        f'Not done: the offer you heard breaks the user\'s rule "{one_line(rule_text)}", and the '
        "user has not approved it. Do not accept it. Counter once, or call ask_user."
    )


REFUSED_NOT_A_FALLBACK = (
    "Not done: the offer you heard matches none of the user's fallbacks. Do not take it as a "
    "fallback. Counter once, or call ask_user."
)
REFUSED_NEEDLESS_COUNTER = (
    "Not done: the offer meets every rule the user typed, so there is nothing to counter. Accept "
    "it yourself with report_decision action accept. If it differs from what you first asked "
    "for, or misses a preference, say what changed in the reason or missed_preference."
)
# "You're all set", "you're booked", "see you Thursday": the business says it is done (G-11).
BOOKED_WORDS = re.compile(
    r"\byou(?:'re| are) (?:all )?(?:set|booked|confirmed)\b"
    r"|\b(?:it's|it is|that's|that is|you're|you are) (?:all )?booked\b"
    r"|\bsee you\b",
    re.IGNORECASE,
)
REFUSED_SECOND_COUNTER = (
    "Not done: you already countered once. Do not ask the business for anything else. Call "
    "ask_user now with what they offered and what the user asked for."
)


def missing_detail_refusal(fields: tuple[str, ...]) -> str:
    """The refusal for settling on an offer that leaves out ``fields`` the rules need."""
    names = " and ".join(f.replace("_", " ") for f in fields)
    return (
        f"Not done: the offer you wrote down has no {names}, and the user's rules need it. "
        f"If the business said it or it is clear from the call (for example the party size "
        f"you asked for and they didn't change), call again with it in offer. If not, ask "
        f"them for the {names} first. Never accept or take a fallback on a guess."
    )


def wrong_day_refusal(reported: date, asked_for: date) -> str:
    """The refusal for a report whose date is not the day the user asked for."""
    return (
        f"Not done: details.date is {reported.isoformat()}, but the user asked for "
        f"{asked_for:%A} {asked_for.day} {asked_for:%B %Y} ({asked_for.isoformat()}) and the "
        f"business never said {reported.isoformat()}. The call is transcribed, so a misheard "
        f"word is not another day. Call report_outcome again with date {asked_for.isoformat()}, "
        "or leave date out if the business agreed to a different day."
    )


REFUSALS = (
    REFUSED_MONEY,
    REFUSED_LOW_OFFER,
    REFUSED_GIVE_UP,
    REFUSED_NEEDLESS_PING,
    REFUSED_UNSAID_NAME,
    REFUSED_NOT_A_FALLBACK,
    REFUSED_SECOND_COUNTER,
    REFUSED_NEEDLESS_COUNTER,
)


@dataclass(frozen=True)
class CallFacts:
    """What the gates need to know about a call so far.

    ``money_since_yes``: the business mentioned money after the user's last approving reply
    (or ever, if there was none). ``latest_ping_approved``: the most recent ping was answered
    with an approving reply. ``user_was_asked``: some ping was answered or expired.
    ``countered_since_answer``: the agent recorded a counter after the user's last answer to
    a decision ping (or ever, if there was none).
    ``ending``: the user pressed End (or the pause cap fired). ``business_said``: every
    business transcript line so far, joined and casefolded. ``instructed``: the user sent an
    instruction during the call (DC-10).
    """

    money_since_yes: bool = False
    latest_ping_approved: bool = False
    user_was_asked: bool = False
    countered_since_answer: bool = False
    ending: bool = False
    business_said: str = ""
    instructed: bool = False


def instruction_covers(covered_by_instruction: object, facts: CallFacts) -> bool:
    """True when the agent marked its action as allowed by the user's own instruction, and the
    user really did send one during this call (DC-10)."""
    return covered_by_instruction is True and facts.instructed


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
    action: str,
    dimensions: Mapping[str, Any] | None,
    facts: CallFacts,
    score: OfferScore | None = None,
    covered_by_instruction: object = None,
) -> str | None:
    """The refusal for accepting an offer that breaks the rules, else None.

    "Breaks the rules" comes from Python's ``score`` where it can tell, and from the model's
    own ``dimensions`` where it can't (or when there is no score). A "yes" from the user to
    the latest ping lets anything through (R-01), and so does an action the agent marks as
    covered by an instruction the user sent during the call (R-09, ``instruction_covers``).
    """
    if facts.latest_ping_approved or instruction_covers(covered_by_instruction, facts):
        return None
    dims: Mapping[str, Any] = dimensions or {}
    meets = score.fits if score is not None and score.fits is not None else None
    if meets is None:
        meets = dims.get("meets_constraints")
    matches = score.matches_fallback if score is not None else None
    if matches is None:
        matches = dims.get("matches_fallback")
    if action == "accept" and meets is False and matches is not True:
        broken = score.broken_constraint if score is not None else None
        return breaks_rule_refusal(broken) if broken else REFUSED_LOW_OFFER
    if action == "take_fallback" and meets is not True and matches is False:
        if score is not None and score.matches_fallback is False:
            return REFUSED_NOT_A_FALLBACK
        if meets is False:
            return REFUSED_LOW_OFFER
    return None


def needless_ping_gate(
    confidence: object,
    dimensions: Mapping[str, Any] | None,
    field_requested: object,
    score: OfferScore | None = None,
    facts: CallFacts | None = None,
) -> str | None:
    """The refusal for asking the user about an offer that already fits, else None.

    "Fits" is the model's own High, or Python finding every constraint passes (C4-06: a
    missed preference is Medium, which the agent takes and flags, never pings). Never when a
    DC-4 gate applies: an ask-first field or money always pings, and so does a ping about
    something the brief can't answer or that the agent didn't understand. Takes the scores
    explicitly, because the live ingress strips them from the tool arguments (DC-5).
    """
    if field_requested or (facts is not None and facts.money_since_yes):
        return None
    if not isinstance(dimensions, Mapping):
        return None
    if dimensions.get("needs_ask_first_field") is not False:
        return None
    if dimensions.get("involves_money") is not False:
        return None
    python_fits = score is not None and score.fits is True
    if python_fits and (
        dimensions.get("answerable_from_brief") is False or dimensions.get("understood") is False
    ):
        return None  # the ping is about something else than the offer
    if confidence == "high" or python_fits:
        return REFUSED_NEEDLESS_PING
    return None


def unsaid_name_gate(arguments: Mapping[str, Any], facts: CallFacts) -> str | None:
    """The refusal for a ``details.staff_name`` the business never said, else None."""
    details = arguments.get("details")
    name = details.get("staff_name") if isinstance(details, Mapping) else None
    if not isinstance(name, str) or not name.strip():
        return None
    folded = " ".join(name.casefold().split())
    if folded in " ".join(facts.business_said.split()):
        return None
    return REFUSED_UNSAID_NAME


def report_day_gate(
    arguments: Mapping[str, Any],
    asked_for: date | None,
    allowed_days: Collection[date],
    facts: CallFacts,
) -> str | None:
    """The refusal for a ``details.date`` that contradicts the day the user asked for, else None.

    The requirement list is the anchor: the date in the report must be the day the user typed
    (``asked_for``), a day one of the brief's date rules allows, or a day the business itself
    named on the call (``business_named_day``, which is generous on purpose). Nothing is checked
    when the user's day couldn't be read from the brief, or when the report has no valid date.
    """
    details = arguments.get("details")
    value = details.get("date") if isinstance(details, Mapping) else None
    try:
        reported = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        reported = None
    if asked_for is None or reported is None or reported == asked_for:
        return None
    if reported in allowed_days or business_named_day(facts.business_said, reported):
        return None
    return wrong_day_refusal(reported, asked_for)


def missing_detail_gate(
    action: str,
    facts: CallFacts,
    score: OfferScore | None,
    covered_by_instruction: object = None,
) -> str | None:
    """The refusal for accepting or taking a fallback when the offer leaves out a value the
    brief's rules need (so Python can't check it), else None. A "yes" from the user to the
    latest ping, or the user's own instruction, lets it through, as in ``low_offer_gate``."""
    if score is None or facts.latest_ping_approved or action not in SETTLING_ACTIONS:
        return None
    if instruction_covers(covered_by_instruction, facts):
        return None
    missing = score.missing_to_settle(action)
    return missing_detail_refusal(missing) if missing else None


def needless_counter_gate(
    dimensions: Mapping[str, Any] | None, score: OfferScore | None
) -> str | None:
    """The refusal for countering an offer Python finds meets every typed rule, else None.

    Only where Python can tell (``score.fits`` is True), and never when the model says money
    or an ask-first field is involved: those go to ask_user, not to an accept.
    """
    if score is None or score.fits is not True:
        return None
    dims: Mapping[str, Any] = dimensions or {}
    if dims.get("involves_money") is True or dims.get("needs_ask_first_field") is True:
        return None
    return REFUSED_NEEDLESS_COUNTER


def business_said_booked(facts: CallFacts) -> bool:
    """True when the business's latest line says it is booked ("You're all set")."""
    latest = facts.business_said.rsplit("\n", 1)[-1]
    return BOOKED_WORDS.search(latest) is not None


def counter_gate(action: object, facts: CallFacts) -> str | None:
    """The refusal for a second counter before the user has answered a ping, else None."""
    if action == "counter" and facts.countered_since_answer:
        return REFUSED_SECOND_COUNTER
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

    Kinds: ``business`` (a business transcript line), ``countered`` (the agent recorded a
    counter), ``answered`` (a decision ping answered, with the reply text),
    ``field_answered`` (an Ask-me-first approval answered: it counts as asking, never as
    approving money or as an answer to a counter), ``expired`` (a ping that timed out) and
    ``instructed`` (the user sent an instruction, DC-10).
    ``latest_ping_answer`` is the reply to the most recent ping if it was a decision ping and
    was answered, else None.
    """
    money_since_yes = False
    asked = False
    countered = False
    instructed = False
    said: list[str] = []
    for kind, text in timeline:
        if kind == "business":
            said.append(text.casefold())
        if kind == "business" and mentions_money(text):
            money_since_yes = True
        elif kind == "countered":
            countered = True
        elif kind == "answered":
            asked = True
            countered = False
            if is_approving(text):
                money_since_yes = False
        elif kind in ("expired", "field_answered"):
            asked = True
        elif kind == "instructed":
            instructed = True
    return CallFacts(
        money_since_yes=money_since_yes,
        latest_ping_approved=latest_ping_answer is not None and is_approving(latest_ping_answer),
        user_was_asked=asked,
        countered_since_answer=countered,
        ending=ending,
        business_said="\n".join(said),
        instructed=instructed,
    )


async def load_brief(conn: AsyncConnection[Any], call_id: UUID) -> dict[str, Any] | None:
    """The reviewed brief (brief.v1 JSON) the call was placed from, or None if it has none."""
    row = await (
        await conn.execute(
            "select b.brief from public.calls c join public.briefs b on b.id = c.brief_id"
            " where c.id = %s",
            (call_id,),
        )
    ).fetchone()
    return row[0] if row is not None and isinstance(row[0], dict) else None


async def load_facts(conn: AsyncConnection[Any], call_id: UUID) -> CallFacts:
    """Read ``CallFacts`` for a call: its business transcript lines and resolved pings."""
    rows = await (
        await conn.execute(
            "select e.type, e.payload, p.kind from public.call_events e"
            " left join public.pings p on p.ping_id::text = e.payload->>'ping_id'"
            " where e.call_id = %s and (e.type = 'ping.resolved'"
            "  or (e.type = 'transcript.final' and e.payload->>'role' = 'business')"
            "  or (e.type = 'agent.decision' and e.payload->>'action' = 'counter')"
            "  or (e.type = 'command.applied' and e.payload->>'command' = 'instruction'))"
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
        elif event_type == "agent.decision":
            timeline.append(("countered", ""))
        elif event_type == "command.applied":
            timeline.append(("instructed", ""))
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

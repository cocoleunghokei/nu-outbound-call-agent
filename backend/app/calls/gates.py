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
- **New proposal** (``new_proposal_gate``): accept or take_fallback is refused when the model's own
  ``answerable_from_brief`` is false: the business proposed something the brief doesn't cover
  (marshmallow cake in place of the mochi the user wanted), which is the user's call, not a
  missed preference.
- **Never-agree evidence** (``never_agree_gate``): accept or take_fallback is refused until
  ``rule_evidence`` quotes a denial the business really said, for every never-agree item that
  isn't about money ("cake with nuts"): in reply to a question the agent asked that names the
  item ("Does that cake have any nuts?" "No, none."), or unprompted ("all our cakes are nut
  free"). A yes isn't enough, because it could mean the item is there. The check reads words,
  it doesn't understand them.
- **Named fallback** (``named_fallback_gate``): ``take_fallback`` is refused unless ``fallback``
  names one of the user's fallbacks as typed, and refused when that fallback's own rules fail
  against the offer or the offer lacks a value they need. A text-only sibling fallback leaves
  Python unable to say that none matches, so without this the model's "it matches a fallback"
  stood (noon taken for "11am pickup is fine"). A fallback with no rules is the model's call.
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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.calls.prompt import one_line
from app.calls.requested_day import business_named_day
from app.calls.scoring import OfferScore, check_item, fix_offer_year, missing_fields
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


REFUSED_NEW_PROPOSAL = (
    "Not done: your own checks say the brief can't answer this. The business proposed "
    "something it doesn't cover (a different item, flavour or option the user never "
    "mentioned), so the user decides. Do not accept it. Call ask_user with what they proposed "
    "and what the user asked for."
)


def never_agree_refusal(unconfirmed: Sequence[str]) -> str:
    """The refusal for settling an offer before the business has said it isn't a never-agree
    item. ``unconfirmed`` are the items as the user typed them."""
    names = "; ".join(f'"{one_line(text)}"' for text in unconfirmed)
    return (
        "Not done: the user said never to agree to these, and the business has not clearly said "
        f"this offer is not one of them: {names}. Ask the business a question that names each "
        '(for example "Does that cake have any nuts?") and wait for the answer. It has to be a no '
        '("No nuts", "None", "It\'s nut free"): a yes, or a vague answer, is not enough. Then call '
        "report_decision again with rule_evidence: one entry per item, rule exactly as the user "
        "typed it and business_said copied from their no. If they can't say, call ask_user."
    )


def fallback_not_named_refusal(options: Sequence[str]) -> str:
    """The refusal for taking a fallback without naming which one. ``options`` are the user's
    fallbacks as typed."""
    listed = "; ".join(f'"{one_line(text)}"' for text in options)
    return (
        "Not done: say which of the user's fallbacks this offer is. Call report_decision again "
        f"with fallback set to exactly one of: {listed}. If none of them fits, do not take "
        "it as a fallback: counter once, or call ask_user."
    )


def fallback_breaks_refusal(text: str) -> str:
    """The refusal for taking a fallback the offer doesn't fit."""
    return (
        f'Not done: the offer you heard breaks the fallback you named, "{one_line(text)}". Do '
        "not take it as that fallback. Counter once, or call ask_user."
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
    instruction during the call (DC-10). ``turns``: every final transcript line in the order it
    was spoken, as (``agent`` or ``business``, text); it lets a gate tell what a business line
    answered.
    """

    money_since_yes: bool = False
    latest_ping_approved: bool = False
    user_was_asked: bool = False
    countered_since_answer: bool = False
    ending: bool = False
    business_said: str = ""
    instructed: bool = False
    turns: tuple[tuple[str, str], ...] = ()


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


def new_proposal_gate(
    action: str,
    dimensions: Mapping[str, Any] | None,
    facts: CallFacts,
    covered_by_instruction: object = None,
) -> str | None:
    """The refusal for accepting something the business proposed that the brief doesn't cover.

    The model says so itself: ``answerable_from_brief`` false (check 5: "can you answer from
    this brief without making anything up?"). Marshmallow cake offered in place of the mochi
    the user asked for is not a missed preference, it is a new thing, and the user decides. A
    "yes" from the user to the latest ping, or their own instruction (R-09), lets it through,
    as in ``low_offer_gate``.
    """
    if action not in SETTLING_ACTIONS or facts.latest_ping_approved:
        return None
    if instruction_covers(covered_by_instruction, facts):
        return None
    if isinstance(dimensions, Mapping) and dimensions.get("answerable_from_brief") is False:
        return REFUSED_NEW_PROPOSAL
    return None


def _normal(text: object) -> str:
    """Casefolded words only: punctuation and repeated spaces don't matter when matching."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).casefold()).split())


# A quote this short ("no") says nothing when nobody asked, so an unprompted quote needs two words.
_MIN_VOLUNTEERED_WORDS = 2
# What a denial looks like: "no nuts", "none of them", "it's nut free", "doesn't have any".
_DENIAL = re.compile(r"\b(?:no|nope|nah|none|not|never|without|nothing|free)\b|n't\b")
# Words that name nothing in particular, so asking about them is not asking about the item.
_FILLER = frozenset(
    "the and for with any that this from have has had are was were you your our their not "
    "but can will would could should may might one two all some more most only also just "
    "than then them they there here what when where which who whom why how".split()
)
_MIN_STEM_LETTERS = 3


def _stems(text: str) -> set[str]:
    """Content words of ``text`` as stems: 3+ letters, no filler words, a plural "s" removed,
    so "nuts" and "nut" meet."""
    words = _normal(text).split()
    stems = {w for w in words if len(w) >= _MIN_STEM_LETTERS and w not in _FILLER}
    return {w[:-1] if w.endswith("s") and len(w) > _MIN_STEM_LETTERS else w for w in stems}


def _item_stems(item: str, brief: Mapping[str, Any] | None) -> set[str]:
    """The words that make ``item`` specific: its stems minus the ones the goal already uses.

    "cake with nuts" in a call about ordering a cake is about "nuts": asking "Is the cake
    ready?" does not ask about the item. If nothing is left, every stem of the item counts.
    """
    stems = _stems(item)
    if brief:
        about = f"{brief.get('goal', '')} {brief.get('task_line', '')}"
        stems = (stems - _stems(about)) or stems
    return stems


def _whole(quote: str, text: str) -> bool:
    """True if the words of ``quote`` appear in ``text`` as whole words, in order."""
    return bool(quote) and f" {quote} " in f" {_normal(text)} "


def _is_denial(quote: str) -> bool:
    return _DENIAL.search(quote.casefold().replace("\u2019", "'")) is not None


def _answers_the_item(
    item: str, quote: str, turns: Sequence[tuple[str, str]], brief: Mapping[str, Any] | None
) -> bool:
    """True if the business really said, in answer to the item, that the offer isn't it.

    The quote has to be a denial (``_DENIAL``: a "yeah" could mean yes, it has nuts), and one of:

    - **Answered:** the agent asked a question that names the item, and the quote is in the
      business's reply to it (the business lines before the agent speaks again).
    - **Volunteered:** the business said it unprompted, in at least two words that name the item.

    The business's words are checked, not understood: "not nut free" would pass. The agent
    still has to ask, and the user can see the call.
    """
    wanted = _normal(quote)
    stems = _item_stems(item, brief)
    if not wanted or not _is_denial(quote) or not stems:
        return False
    for index, (role, text) in enumerate(turns):
        if role == "agent" and "?" in text and _stems(text) & stems:
            reply = []
            for later_role, later_text in turns[index + 1 :]:
                if later_role == "agent":
                    break
                reply.append(later_text)
            if _whole(wanted, " ".join(reply)):
                return True
        if (
            role == "business"
            and len(wanted.split()) >= _MIN_VOLUNTEERED_WORDS
            and _stems(quote) & stems
            and _whole(wanted, text)
        ):
            return True
    return False


def never_agree_to_check(brief: Mapping[str, Any] | None) -> list[str]:
    """The brief's never-agree items the business has to speak to: every one that isn't about
    money. Money (a deposit, a fee, a charge) already has its own gate (``money_gate``)."""
    items = brief.get("never_agree") if brief else None
    texts = (
        [i.get("text") for i in items if isinstance(i, Mapping)] if isinstance(items, list) else []
    )
    return [
        t.strip() for t in texts if isinstance(t, str) and t.strip() and not MONEY_WORDS.search(t)
    ]


def never_agree_gate(
    action: str,
    arguments: Mapping[str, Any],
    brief: Mapping[str, Any] | None,
    facts: CallFacts,
    covered_by_instruction: object = None,
) -> str | None:
    """The refusal for accepting or taking a fallback before the business has said, in its own
    words and in answer to the item, that the offer is not one of the user's never-agree items.

    Python can't read "cake with nuts", so the agent must bring evidence: ``rule_evidence``
    with one entry per item (the item exactly as typed, and what the business said). The quote
    must be a denial the business really said, either in reply to a question the agent asked
    that names the item, or unprompted (``_answers_the_item``). A "yes" from the user to the
    latest ping, or their own instruction, lets it through, as in ``low_offer_gate``.
    """
    if action not in SETTLING_ACTIONS or facts.latest_ping_approved:
        return None
    if instruction_covers(covered_by_instruction, facts):
        return None
    required = never_agree_to_check(brief)
    if not required:
        return None
    evidence = arguments.get("rule_evidence")
    quotes: dict[str, str] = {}
    for entry in evidence if isinstance(evidence, list) else []:
        if isinstance(entry, Mapping):
            quotes[_normal(entry.get("rule", ""))] = str(entry.get("business_said", ""))
    unconfirmed = [
        text
        for text in required
        if not _answers_the_item(text, quotes.get(_normal(text), ""), facts.turns, brief)
    ]
    return never_agree_refusal(unconfirmed) if unconfirmed else None


# A leading list number the agent may copy from <fallbacks> ("1. Friday morning").
_LIST_NUMBER = re.compile(r"^\s*\d+\s*[.)]\s+")


def named_fallback_gate(
    action: str,
    arguments: Mapping[str, Any],
    brief: Mapping[str, Any] | None,
    facts: CallFacts,
    covered_by_instruction: object = None,
) -> str | None:
    """The refusal for taking a fallback that isn't named, or that the offer doesn't fit.

    While any of the user's fallbacks is words only, Python can't say that *no* fallback
    matches ("tea flavoured cake" might), so the model's "it matches a fallback" would stand:
    noon was taken as a fallback for "11am pickup is fine". So ``take_fallback`` must name the
    one fallback it takes (``fallback``, as the user typed it), and that fallback's own rules
    are checked against the offer: refused when they fail, or when the offer leaves out a
    value they need. A fallback with no rules is the model's call. A "yes" from the user to
    the latest ping, or their own instruction, lets it through, as in ``low_offer_gate``.
    """
    if action != "take_fallback" or facts.latest_ping_approved:
        return None
    if instruction_covers(covered_by_instruction, facts):
        return None
    raw = brief.get("fallbacks") if brief else None
    items = [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []
    texts = [str(i.get("text", "")).strip() for i in items if str(i.get("text", "")).strip()]
    if not texts:
        return None  # nothing to name: the other gates judge a fallback that doesn't exist
    named = _normal(_LIST_NUMBER.sub("", str(arguments.get("fallback") or "")))
    item = next((i for i in items if _normal(i.get("text", "")) == named and named), None)
    if item is None:
        return fallback_not_named_refusal(texts)
    offer = arguments.get("offer")
    offer = offer if isinstance(offer, Mapping) else {}
    if brief is not None and offer:
        offer = fix_offer_year(brief, offer)
    text = str(item.get("text", ""))
    check = check_item(item, offer)
    if check == "fail":
        return fallback_breaks_refusal(text)
    missing = missing_fields(item, offer) if check == "unknown" else ()
    return missing_detail_refusal(missing) if missing else None


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

    Kinds: ``business`` and ``agent`` (a transcript line), ``countered`` (the agent recorded a
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
    turns: list[tuple[str, str]] = []
    for kind, text in timeline:
        if kind in ("business", "agent"):
            turns.append((kind, text))
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
        turns=tuple(turns),
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
            "  or (e.type = 'transcript.final' and e.payload->>'role' in ('business', 'agent'))"
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
            role = "agent" if payload.get("role") == "agent" else "business"
            timeline.append((role, str(payload.get("text") or "")))
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

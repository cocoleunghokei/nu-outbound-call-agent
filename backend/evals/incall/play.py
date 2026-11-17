"""Plays one case against the model, the way a live call would unfold. No phone, no database.

The conversation starts with the real system prompt and the opening line as the first
assistant turn. Each script step is then sent in order: a business line as a user turn, a
system message (the user's reply, a timeout, an instruction) as a system turn, worded by
``app/calls/messages.py``. After each step the model replies; whenever it calls tools, each
call gets the result the backend would return, and the model is called again for its next
reply, as Vapi does.

The tool results follow the backend:

- ``ask_user`` opens a ping (replacing any open one) and returns ``ask_user_result``. For an
  ask-first field the user sees the server's own question ("They're asking for your date of
  birth"), so a later reply quotes that, not the model's wording (``app/calls/ask.py``).
- ``report_decision`` with accept or take_fallback withdraws an open decision ping and returns
  ``question_withdrawn_result``; otherwise "Noted." (``app/calls/decisions.py``).
- ``report_outcome`` returns "Noted."; ``endCall`` ends the run.

A run stops early, and is judged as it stands, when the model leaves the script: it ends the
call, or a reply or timeout step arrives with no open ping to answer.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.calls import messages
from app.calls.decisions import NOTED, SETTLING_ACTIONS
from evals.incall.model import ChatModel, ModelError
from evals.incall.records import Event, ModelReply, RunRecord, Turn, TurnType
from evals.incall.setup import EvalData, call_setup

# Our server's share of a decision turn's silence (CASES.md, latency: decision turns).
SERVER_S = 0.5
# Model calls allowed for one step: a reply, its tool results, and a few follow-ups.
MAX_CALLS_PER_STEP = 5
END_CALL = "endCall"
CALL_ENDED_RESULT = "Call ended."


@dataclass
class _Ping:
    question: str
    kind: str  # "decision" or "field_approval"


@dataclass
class _State:
    ping: _Ping | None = None
    ended: bool = False


def system_text(data: EvalData, step: dict[str, Any], ping: _Ping | None) -> str | None:
    """The system message for a script step, or None when it needs an open ping and has none."""
    name = data.calling_name
    kind = step["system"]
    if kind == "instruction":
        return f"{name}'s instruction: {step['text']}."  # Q3 in CASES.md
    if ping is None:
        return None
    if kind == "timeout":
        return messages.timeout_message()
    if kind == "answer":
        return messages.answer_message(name, ping.question, step["reply"])
    field = step["field"]
    label = messages.field_label(field)
    question = messages.field_approval_question(field)[0]
    if kind == "field_shared":
        value = data.ask_first_values()[field]
        return messages.field_shared_message(name, question, label, value)
    return messages.field_declined_message(name, question, label)


def tool_result(data: EvalData, state: _State, name: str, args: dict[str, Any] | None) -> str:
    """What the backend returns for one tool call, updating the run's ping state."""
    if state.ended:
        return messages.call_over_result()
    args = args or {}
    if name == "ask_user":
        field = args.get("field_requested")
        if isinstance(field, str) and field in data.ask_first_values():
            state.ping = _Ping(messages.field_approval_question(field)[0], "field_approval")
        else:
            state.ping = _Ping(str(args.get("question", "")).strip(), "decision")
        return messages.ask_user_result(data.calling_name)
    if name == "report_decision":
        settles = args.get("action") in SETTLING_ACTIONS
        if settles and state.ping is not None and state.ping.kind == "decision":
            state.ping = None
            return messages.question_withdrawn_result()
        return NOTED
    if name == END_CALL:
        state.ended = True
        return CALL_ENDED_RESULT
    return NOTED  # report_outcome, or an unknown tool


def _assistant_message(reply: ModelReply) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.raw_args},
            }
            for call in reply.tool_calls
        ]
    return message


def measure_turn(step: int, is_system: bool, replies: list[ModelReply]) -> Turn:
    """The silence the business heard after one step, from the model calls it took.

    Speaking first: the time to the first spoken token. Calling a tool without speaking: the
    time until the tool call was complete, plus ``SERVER_S``, plus the next call's time to its
    first token (CASES.md, latency). Time to ping: when the ``ask_user`` call was complete.
    """
    has_tool = any(r.tool_calls for r in replies)
    kind: TurnType = "after_system" if is_system else ("decision" if has_tool else "talk")
    silence: float | None = None
    elapsed = 0.0  # time since the step, at the start of each model call
    tool_first = bool(replies) and bool(replies[0].tool_calls) and replies[0].first_token_s is None
    ping_at: float | None = None
    for reply in replies:
        if ping_at is None and any(c.name == "ask_user" for c in reply.tool_calls):
            ping_at = elapsed + reply.done_s
        if silence is None and reply.first_token_s is not None:
            silence = elapsed + reply.first_token_s
        elapsed += reply.done_s + SERVER_S
    if silence is None and tool_first:
        silence = elapsed  # tools only, nothing spoken: the business heard the whole wait
    return Turn(
        step=step,
        type=kind,
        silence_s=silence,
        tool_before_speaking=tool_first,
        time_to_ping_s=ping_at,
    )


async def play_case(
    data: EvalData,
    case: dict[str, Any],
    model: ChatModel,
    run: int,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunRecord:
    """Play one run of one case and return everything it did."""
    setup = call_setup(data, case)
    record = RunRecord(
        case_id=case["id"],
        run=run,
        model=model.name,
        temperature=model.temperature,
        started_at=now().isoformat(timespec="seconds"),
    )
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": setup.system_prompt},
        {"role": "assistant", "content": setup.first_message},
    ]
    record.events.append(Event(step=0, kind="agent", text=setup.first_message))
    state = _State()

    for number, step in enumerate(case["steps"], start=1):
        if "business" in step:
            convo.append({"role": "user", "content": step["business"]})
            record.events.append(Event(step=number, kind="business", text=step["business"]))
        else:
            text = system_text(data, step, state.ping)
            if text is None:
                record.stopped = f"step {number}: no open ask_user for this {step['system']}"
                break
            if step["system"] in ("answer", "field_shared", "field_declined", "timeout"):
                state.ping = None  # the backend closes the ping it answers
            convo.append({"role": "system", "content": text})
            record.events.append(
                Event(step=number, kind="system", text=text, system_kind=step["system"])
            )
        try:
            replies = await _respond(data, model, setup.tools, convo, record, state, number)
        except ModelError as exc:
            record.error = str(exc)
            break
        record.turns.append(measure_turn(number, "system" in step, replies))
        if state.ended:
            if number < len(case["steps"]):
                record.stopped = f"step {number}: the model ended the call"
            break
    return record


async def _respond(
    data: EvalData,
    model: ChatModel,
    tools: list[dict[str, Any]],
    convo: list[dict[str, Any]],
    record: RunRecord,
    state: _State,
    step: int,
) -> list[ModelReply]:
    """The model's replies to one step: one call, plus one more after each round of tools."""
    replies: list[ModelReply] = []
    for _ in range(MAX_CALLS_PER_STEP):
        reply = await model.reply(convo, tools)
        replies.append(reply)
        record.usage.append(reply.usage)
        convo.append(_assistant_message(reply))
        if reply.text:
            record.events.append(Event(step=step, kind="agent", text=reply.text))
        if not reply.tool_calls:
            break
        for call in reply.tool_calls:
            record.events.append(
                Event(
                    step=step,
                    kind="tool_call",
                    tool=call.name,
                    args=call.args,
                    call_id=call.call_id,
                )
            )
            result = tool_result(data, state, call.name, call.args)
            convo.append({"role": "tool", "tool_call_id": call.call_id, "content": result})
            record.events.append(
                Event(
                    step=step, kind="tool_result", tool=call.name, text=result, call_id=call.call_id
                )
            )
        if state.ended:
            break
    return replies

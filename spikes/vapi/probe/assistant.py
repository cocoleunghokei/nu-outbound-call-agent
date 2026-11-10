"""Build the inline Vapi assistant config for a scenario."""

from __future__ import annotations

from probe.config import Settings
from probe.scenario import Scenario

SECRET_HEADER = "x-probe-secret"
ASK_USER_TOOL_NAME = "ask_user"
REPORT_DECISION_TOOL_NAME = "report_decision"
REPORT_OUTCOME_TOOL_NAME = "report_outcome"
CONFIDENCE_LEVELS = ["high", "medium", "low"]
DECISION_ACTIONS = ["accept", "counter", "decline", "share_field", "take_fallback", "end_call"]
OUTCOMES = ["booked", "cancelled", "rescheduled", "needs_follow_up", "refused_ai", "wrong_number"]
DIMENSION_KEYS = ["needs_ask_first_field", "involves_money", "meets_constraints",
                  "matches_fallback", "answerable_from_brief", "understood"]
SERVER_MESSAGES = (
    "status-update",
    "speech-update",
    "transcript",
    "tool-calls",
    "end-of-call-report",
    "conversation-update",
    "hang",
)


def disclosure_line(scenario: Scenario) -> str:
    """Opening line: AI disclosure, transcription notice, then the request (DC-1)."""
    return (
        f"Hi, I'm an AI assistant calling on behalf of {scenario.user_name}. "
        f"This call is transcribed. {scenario.opening_request}"
    )


def _bullets(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def system_prompt(scenario: Scenario) -> str:
    """The system prompt: brief, DC-4 decision rule, guardrails and no-reply rule."""
    share = tuple(f"{f.name}: {f.value}" for f in scenario.profile if f.share == "share")
    ask = tuple(f"{f.name}: {f.value}" for f in scenario.profile if f.share == "ask")
    user = scenario.user_name
    tool = ASK_USER_TOOL_NAME
    return f"""You are an AI assistant making a phone call to {scenario.business_name} on behalf of {user}.
Your goal: {scenario.goal}.
Speak in short, natural sentences. This is a live phone call.

SHARE FREELY (you may give these when asked or when needed):
{_bullets(share)}

ASK FIRST (never say these until you have called {tool} and {user} has approved sharing them):
{_bullets(ask)}

HARD CONSTRAINTS (the outcome must satisfy all of these):
{_bullets(scenario.constraints)}

FALLBACKS (acceptable alternatives if the first choice is not possible):
{_bullets(scenario.fallbacks)}

NEVER AGREE TO ON YOUR OWN:
{_bullets(scenario.never_agree)}
If the business asks for any of these, do not agree and do not flatly refuse. Call {tool}, and agree only if {user}'s reply approves.

DECIDING WHAT TO DO (DC-4). For every offer or question from the business, answer 6 questions.
TWO GATES, which always ping. If either is yes, call {tool} whatever else is true:
- Does it need an Ask-me-first field? Set field_requested to that field's name.
- Does it involve money, a deposit, a fee or a never-agree item?
FOUR CHECKS, when neither gate applies:
- Does the offer meet every hard constraint?
- Does it match a listed fallback?
- Can you answer without making anything up (from this brief only)?
- Did you understand what they said? If not, ask them to repeat once before deciding.
RESULT. There is no numeric threshold; pick the tier that fits. At a gate, set confidence to your best tier from the four checks.

- High: the offer meets the constraints or matches a fallback, you can answer from the brief and you understood. Act on your own, then call report_decision with confidence high.
- Medium: every check passes but a preference is missed. Act on your own, then call report_decision with confidence medium and put the missed preference in missed_preference.
- Low: a constraint is broken and no fallback matches, or information is missing. Call {tool} with confidence low. Give it a question, a plain-language reason and 2 to 4 suggested replies.
Re-check every counter-offer. Both {tool} and report_decision take a dimensions object with your 6 answers (needs_ask_first_field, involves_money, meets_constraints, matches_fallback, answerable_from_brief, understood).
After you call {tool}, say nothing further; the system will play hold lines for you. Wait until you are told that {user} replied or did not reply. If the other side speaks while you wait, say only that you are still checking.
Call report_decision after you act without asking; do not wait for a reply to it.
Call report_outcome once before ending the call: outcome, plus business_confirmed true only if the other side explicitly confirmed it in words. Then end the call.

RULES:
- You must never claim to be human. If asked "are you a robot?" or similar, say honestly that you are an AI assistant.
- Never share or invent details that are not in this brief. If you do not know something, say so.
- Call {tool} before agreeing to any payment, deposit or fee.
- If the business refuses to talk to an AI, apologise, say goodbye, then call report_outcome first (refused_ai), then call the endCall tool.
- If you reach an automated phone menu, use the dtmf tool to press the option that reaches a person.

IF {user.upper()} DOES NOT REPLY:
If you are told that {user} did not reply, apply this rule. If the latest offer matches a listed fallback, accept it and say which fallback it matched. Otherwise say goodbye politely, say you will confirm with {user} and call back, then call report_outcome first (needs_follow_up), then call the endCall tool. Do not guess.
"""


def _text(description: str, max_length: int = 200) -> dict:
    return {"type": "string", "maxLength": max_length, "description": description}


def _confidence() -> dict:
    return {"type": "string", "enum": list(CONFIDENCE_LEVELS),
            "description": "High, medium or low, from the DC-4 checks."}


def _dimensions() -> dict:
    return {
        "type": "object",
        "description": "Your yes/no answers to the 6 DC-4 questions. Logged, never shown.",
        "properties": {k: {"type": "boolean"} for k in DIMENSION_KEYS},
        "required": list(DIMENSION_KEYS),
        "additionalProperties": False,
    }


def _ask_user_tool(scenario: Scenario, server: dict) -> dict:
    return {
        "type": "function",
        "async": True,
        "function": {
            "name": ASK_USER_TOOL_NAME,
            "description": (
                f"Ask {scenario.user_name} a question and wait for the reply. Use at a gate "
                "(an ASK FIRST field, or money, a deposit, a fee or a never-agree item) or at "
                "Low confidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": _text("The question to ask the user."),
                    "reason": _text("Plain-language reason for asking."),
                    "suggested_replies": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 60},
                        "minItems": 2,
                        "maxItems": 4,
                        "description": "Two to four short reply options.",
                    },
                    "confidence": _confidence(),
                    "field_requested": {
                        "type": "string",
                        "description": "Profile field key when an Ask-me-first field triggers "
                                       "the ping.",
                    },
                    "dimensions": _dimensions(),
                },
                "required": ["question", "reason", "suggested_replies", "confidence"],
            },
        },
        "server": dict(server),
    }


def _report_decision_tool(server: dict) -> dict:
    return {
        "type": "function",
        "async": True,
        "function": {
            "name": REPORT_DECISION_TOOL_NAME,
            "description": ("Tell the system what you decided on your own at High or Medium "
                            "confidence. Fire and forget; do not wait for a reply."),
            "parameters": {
                "type": "object",
                "properties": {
                    "situation": _text("What the business offered or asked."),
                    "action": {"type": "string", "enum": list(DECISION_ACTIONS),
                               "description": "What you did."},
                    "confidence": _confidence(),
                    "reason": _text("Why you chose this action."),
                    "field_key": {"type": "string",
                                  "description": "Profile field key, when sharing a field."},
                    "missed_preference": _text("The preference missed at Medium, shown as a flag "
                                               "in the live feed."),
                    "dimensions": _dimensions(),
                },
                "required": ["situation", "action", "confidence", "reason"],
            },
        },
        "server": dict(server),
    }


def _report_outcome_tool(server: dict) -> dict:
    return {
        "type": "function",
        "async": True,
        "function": {
            "name": REPORT_OUTCOME_TOOL_NAME,
            "description": "Report how the call went. Call once, before ending the call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": list(OUTCOMES),
                                "description": "How the call ended."},
                    "business_confirmed": {
                        "type": "boolean",
                        "description": "True only if the other side explicitly confirmed in "
                                       "words.",
                    },
                    "details": {
                        "type": "object",
                        "description": "Agreed specifics, if any.",
                        "properties": {k: {"type": "string"} for k in
                                       ("date", "time", "confirmation_number", "staff_name")},
                    },
                    "open_items": {"type": "array", "items": {"type": "string"},
                                   "description": "Anything still unresolved."},
                },
                "required": ["outcome", "business_confirmed"],
            },
        },
        "server": dict(server),
    }


def build_assistant(scenario: Scenario, settings: Settings) -> dict:
    """Return the inline assistant config sent with each outbound call."""
    server_url = settings.public_url + "/webhook"
    headers = {SECRET_HEADER: settings.webhook_secret}
    server = {"url": server_url, "headers": dict(headers)}
    tools = [_ask_user_tool(scenario, server), _report_decision_tool(server),
             _report_outcome_tool(server), {"type": "endCall"}, {"type": "dtmf"}]
    return {
        "firstMessage": disclosure_line(scenario),
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": settings.model,
            "messages": [{"role": "system", "content": system_prompt(scenario)}],
            "tools": tools,
        },
        "voice": {"provider": "vapi", "voiceId": "Clara"},
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en-US"},
        "server": {"url": server_url, "headers": dict(headers)},
        "serverMessages": list(SERVER_MESSAGES),
        "artifactPlan": {"recordingEnabled": False, "transcriptPlan": {"enabled": True}},
        "monitorPlan": {"listenEnabled": True, "controlEnabled": True},
        "maxDurationSeconds": 600,
    }

"""Build the inline Vapi assistant config for a scenario."""

from __future__ import annotations

from probe.config import Settings
from probe.scenario import Scenario

SECRET_HEADER = "x-probe-secret"
ASK_USER_TOOL_NAME = "ask_user"
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
    """The system prompt: brief, ask rule, guardrails and no-reply rule."""
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

WHEN TO CALL {tool}:
Call {tool} when either of these is true:
- an offer breaks a hard constraint and does not match a listed fallback
- the other side asks for an ASK FIRST field
Otherwise, act on your own. Give {tool} a question, a plain-language reason and 2 to 4 suggested replies.
After you call {tool}, say nothing further; the system will play hold lines for you. Wait until you are told that {user} replied or did not reply. If the other side speaks while you wait, say only that you are still checking.

RULES:
- You must never claim to be human. If asked "are you a robot?" or similar, say honestly that you are an AI assistant.
- Never share or invent details that are not in this brief. If you do not know something, say so.
- Call {tool} before agreeing to any payment, deposit or fee.
- If the business refuses to talk to an AI, apologise, say goodbye, then call the endCall tool.
- If you reach an automated phone menu, use the dtmf tool to press the option that reaches a person.

IF {user.upper()} DOES NOT REPLY:
If you are told that {user} did not reply, apply this rule. If the latest offer matches a listed fallback, accept it and say which fallback it matched. Otherwise say goodbye politely, say you will confirm with {user} and call back, then call the endCall tool. Do not guess.
"""


def build_assistant(scenario: Scenario, settings: Settings) -> dict:
    """Return the inline assistant config sent with each outbound call."""
    server_url = settings.public_url + "/webhook"
    headers = {SECRET_HEADER: settings.webhook_secret}
    tool = {
        "type": "function",
        "async": True,
        "function": {
            "name": ASK_USER_TOOL_NAME,
            "description": (
                f"Ask {scenario.user_name} a question and wait for the reply. Use when an "
                "offer breaks a hard constraint with no matching fallback, or when asked "
                "for an ASK FIRST field."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask the user."},
                    "reason": {
                        "type": "string",
                        "description": "Plain-language reason for asking.",
                    },
                    "suggested_replies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 4,
                        "description": "Two to four short reply options.",
                    },
                },
                "required": ["question", "reason", "suggested_replies"],
            },
        },
        "server": {"url": server_url, "headers": dict(headers)},
    }
    return {
        "firstMessage": disclosure_line(scenario),
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": settings.model,
            "messages": [{"role": "system", "content": system_prompt(scenario)}],
            "tools": [tool, {"type": "endCall"}, {"type": "dtmf"}],
        },
        "voice": {"provider": "vapi", "voiceId": "Clara"},
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en-US"},
        "server": {"url": server_url, "headers": dict(headers)},
        "serverMessages": list(SERVER_MESSAGES),
        "artifactPlan": {"recordingEnabled": False, "transcriptPlan": {"enabled": True}},
        "monitorPlan": {"listenEnabled": True, "controlEnabled": True},
        "maxDurationSeconds": 600,
    }

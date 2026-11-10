"""The Vapi assistant config for one call: opening line, system prompt and safe defaults.

Pure: no database, no network. Call creation (next PR) loads the brief and the profile
snapshot, calls ``build_assistant_config``, then hands the result to the voice port, whose
``prepare_assistant`` adds the tool definitions and re-checks recording and privacy.

Raises before anything is built when the call can't start safely:
``MissingVar`` (no calling name, or a task variable without a value, DC-1) and
``AskFirstInPrompt`` (an ask-first value would reach the model, DC-6).
"""

from typing import Any

from app.calls.prompt import build_system_prompt
from app.contracts.generated import Brief
from app.voice.prompt_safety import Snapshot, assert_prompt_safe, build_first_message
from app.webhooks.vapi import SECRET_HEADER

# The Vapi server messages the webhook ingress and app/calls handle.
SERVER_MESSAGES = (
    "status-update",
    "speech-update",
    "transcript",
    "tool-calls",
    "end-of-call-report",
)
# Model, voice and transcriber as used in the probe kit; tune after the Vapi session.
MODEL = {"provider": "openai", "model": "gpt-5.4-mini"}
VOICE = {"provider": "vapi", "voiceId": "Clara"}
TRANSCRIBER = {"provider": "deepgram", "model": "nova-3", "language": "en-US"}


def build_assistant_config(
    brief: Brief,
    snapshot: Snapshot,
    *,
    server_url: str,
    webhook_secret: str,
    max_duration_s: int,
) -> dict[str, Any]:
    """Return the inline assistant for this call, without tools (the voice port adds them).

    ``webhook_secret`` goes in the server headers Vapi sends back to ``/webhooks/vapi``; it is
    a secret: never log the returned config.
    """
    first_message = build_first_message(brief.model_dump(mode="json"), snapshot)
    prompt = build_system_prompt(brief, snapshot)
    assert_prompt_safe(first_message, snapshot)
    assert_prompt_safe(prompt, snapshot)
    server = {"url": server_url, "headers": {SECRET_HEADER: webhook_secret}}
    return {
        "firstMessage": first_message,
        "firstMessageMode": "assistant-speaks-first",
        "model": {**MODEL, "messages": [{"role": "system", "content": prompt}]},
        "voice": dict(VOICE),
        "transcriber": dict(TRANSCRIBER),
        "server": server,
        "serverMessages": list(SERVER_MESSAGES),
        # DC-1: transcribed, never recorded. prepare_assistant refuses anything else.
        "artifactPlan": {"recordingEnabled": False, "transcriptPlan": {"enabled": True}},
        "monitorPlan": {"listenEnabled": True, "controlEnabled": True},
        "maxDurationSeconds": max_duration_s,
    }

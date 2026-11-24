"""Flag business speech that looks like a prompt-injection attempt (DC-13), for review only.

The in-call model is told to ignore such speech and carry on (``prompt.GUARDRAIL_RULES``).
This module doesn't block or change anything on the call. It names which patterns a final
transcript line matched, so the webhook can log them and a person can review the call later.
The log carries only the pattern names, never the spoken text, because transcripts can hold
personal data.

The patterns are deliberately narrow. A false positive only costs a log line, but a noisy log
stops being read.
"""

import re

_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b[^.?!]{0,40}"
            r"\b(instructions?|rules?|prompts?|guidelines?|programming)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "claims_authority",
        re.compile(
            r"\b(i am|i'm|this is)\s+(the\s+|your\s+)?"
            r"(system|developer|admin|administrator|operator|supervisor of the ai)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fake_system_message",
        re.compile(
            r"\b(system\s+(message|prompt|override|note)|new\s+instructions?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "grants_permission",
        re.compile(
            r"\byou(\s+are|'re)\s+(now\s+)?(authori[sz]ed|allowed|permitted|cleared)\s+to\b",
            re.IGNORECASE,
        ),
    ),
    (
        "asks_for_prompt",
        re.compile(
            r"\b(repeat|read|recite|reveal|show|print|tell me)\b[^.?!]{0,30}"
            r"\b(your|the)\s+(system\s+prompt|instructions|prompt|rules)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # "Alex already told us the $50 no-show fee is fine" (PI-03): the business claims the
        # user approved something, so the agent should skip asking.
        "claims_user_approval",
        re.compile(
            r"\b(already|previously)\s+(told|said|agreed|approved|confirmed|okayed)\b"
            r"[^.?!]{0,60}\b(fine|ok|okay|good|approved|agreed|alright|all right)\b"
            r"|\b(already|previously)\s+(approved|okayed|authori[sz]ed|signed off on)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "mode_switch",
        re.compile(r"\b(developer|debug|jailbreak|god)\s+mode\b|\byou are now\b", re.IGNORECASE),
    ),
)


def injection_signals(text: str) -> tuple[str, ...]:
    """The names of the injection patterns ``text`` matches, in a fixed order; empty if none."""
    return tuple(name for name, pattern in _SIGNALS if pattern.search(text))

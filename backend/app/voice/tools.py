"""Tool definitions for the in-call model, built from the contract schemas."""

from typing import Any

from app.contracts import load_schema

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "ask_user": (
        "Ask the user when an offer breaks a hard constraint with no matching fallback, when "
        "the other side asks for an ask-first field, or before agreeing to any payment, deposit "
        "or fee, or to a never-agree item, whatever your confidence."
    ),
    "report_decision": (
        "Report every action you take on your own after scoring an offer or question against "
        "the brief. Call ask_user instead whenever the score is Low, and always for an "
        "ask-first field or before agreeing to any payment, deposit or fee, or to a never-agree "
        "item. When you act at Medium (every check passes but a preference is missed), set "
        "missed_preference to the preference you could not meet, in plain language and without "
        'any score, for example "No window seat, patio only"; it is shown as a flag in the '
        "live feed."
    ),
    "report_outcome": (
        "Call once, before you end the call, to report how the call went. Set "
        "business_confirmed to true only if the other side explicitly confirmed the result in "
        "words."
    ),
}


# The only keys Vapi's call API accepts at the top of ``function.parameters``. The contract
# files are full JSON Schema documents (``title`` and so on); sending one as-is is a 400 from
# POST /call, which the backend records as a failed call before anything rings.
_PARAMETER_KEYS = ("type", "properties", "required")


def _vapi_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the OpenAI-style parameters object Vapi accepts, built from a contract schema."""
    return {key: schema[key] for key in _PARAMETER_KEYS if key in schema}


def tool_definitions() -> list[dict[str, Any]]:
    """Return the three async function tools plus Vapi's built-in ``endCall`` tool."""
    functions: list[dict[str, Any]] = [
        {
            "type": "function",
            "async": True,
            "function": {
                "name": name,
                "description": description,
                "parameters": _vapi_parameters(load_schema(f"tools/{name}.args.v1.json")),
            },
        }
        for name, description in _TOOL_DESCRIPTIONS.items()
    ]
    return [*functions, {"type": "endCall"}]

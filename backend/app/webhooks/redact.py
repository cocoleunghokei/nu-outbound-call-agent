"""Scrub secrets from anything that is stored or logged (docs/architecture.md section 4.3).

``redact`` works on JSON-like values (Vapi bodies, tool arguments); ``redact_text`` works on
one string (exception text, a formatted log line). Both apply, in order:

1. every literal occurrence of a known secret (capability URLs, API keys) becomes
   ``[redacted]``, in its plain and its JSON-escaped form (``https:\\/\\/...``);
2. (``redact`` only) the string value of any key matching
   ``(?i)(url|secret|token|authorization|signature|password)`` becomes ``[redacted]``;
3. any ``http(s)://`` URL with a query parameter named like token, key, sig or secret
   becomes ``[redacted]``.

Before the rules run, ``redact`` decodes embedded JSON: a string that parses as a JSON object
or array is decoded, redacted recursively and re-encoded in place. ``function.arguments``
(the one JSON-bearing field Vapi sends) that fails to parse becomes
``[redacted:undecodable]``; any other unparsable string is kept and gets the rules.
"""

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

MARKER = "[redacted]"
UNDECODABLE = "[redacted:undecodable]"

# Secrets shorter than this are not replaced literally: a 3-character "secret" would shred
# ordinary text. Real webhook secrets, API keys and capability URLs are far longer.
MIN_SECRET_LENGTH = 8

_SENSITIVE_KEY = re.compile(r"(?i)(url|secret|token|authorization|signature|password)")
_SENSITIVE_PARAM = re.compile(r"(?i)(token|key|sig|secret)")
# Plain (https://) and JSON-escaped (https:\/\/) URLs, up to whitespace, a quote or a bracket.
_URL = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>\\]*(?:\\/[^\s"'<>\\]*)*""", re.IGNORECASE)


def _secret_forms(secrets: Iterable[str]) -> list[str]:
    """Every secret in its plain and JSON-escaped forms, longest first."""
    forms: set[str] = set()
    for secret in secrets:
        if len(secret) < MIN_SECRET_LENGTH:
            continue
        forms.add(secret)
        forms.add(secret.replace("/", "\\/"))
        forms.add(json.dumps(secret)[1:-1])
        forms.add(json.dumps(secret, ensure_ascii=False)[1:-1])
    return sorted(forms, key=len, reverse=True)


def _redact_token_url(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        query = urlsplit(url.replace("\\/", "/")).query
    except ValueError:
        return MARKER
    for name, _ in parse_qsl(query, keep_blank_values=True):
        if _SENSITIVE_PARAM.search(name):
            return MARKER
    return url


def _apply_text_rules(text: str, forms: list[str]) -> str:
    for form in forms:
        if form in text:
            text = text.replace(form, MARKER)
    return _URL.sub(_redact_token_url, text)


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """Return ``text`` with every secret and every token-bearing URL replaced (rules 1 and 3)."""
    return _apply_text_rules(text, _secret_forms(secrets))


def _decode_embedded(text: str) -> Any | None:
    """Return the decoded JSON object or array in ``text``, or None if it holds neither."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict | list) else None


def _redact_string(text: str, forms: list[str], *, json_field: bool) -> str:
    decoded = _decode_embedded(text)
    if decoded is not None:
        return json.dumps(_walk(decoded, forms, parent=None), ensure_ascii=False)
    if json_field:
        return UNDECODABLE
    return _apply_text_rules(text, forms)


def _walk(node: Any, forms: list[str], parent: str | None) -> Any:
    if isinstance(node, dict):
        out: dict[Any, Any] = {}
        for key, value in node.items():
            name = key if isinstance(key, str) else ""
            if isinstance(value, str):
                if parent == "function" and name == "arguments":
                    out[key] = _redact_string(value, forms, json_field=True)
                elif _SENSITIVE_KEY.search(name):
                    out[key] = MARKER
                else:
                    out[key] = _redact_string(value, forms, json_field=False)
            else:
                out[key] = _walk(value, forms, parent=name)
        return out
    if isinstance(node, list | tuple):
        return [_walk(item, forms, parent=parent) for item in node]
    if isinstance(node, str):
        return _redact_string(node, forms, json_field=False)
    return node


def redact(obj: Any, secrets: Iterable[str]) -> Any:
    """Return a redacted deep copy of ``obj`` (dicts, lists, strings, scalars).

    The input is never modified. See the module docstring for the rules.
    """
    return _walk(obj, _secret_forms(secrets), parent=None)

"""Shared test fixtures for the probe kit."""

FULL_ENV = {
    "VAPI_API_KEY": "test-key",
    "VAPI_PHONE_NUMBER_ID": "pn_test",
    "VAPI_WEBHOOK_SECRET": "test-secret",
    "PROBE_PUBLIC_URL": "https://example.trycloudflare.com",
    "PROBE_RECEPTIONIST_NUMBERS": "+16175550101,+16175550102",
    "PROBE_OPERATOR_NUMBER": "+16175550199",
}

from pathlib import Path

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"


class FakeTransport:
    """Scripted Transport: pops one response (or raises an Exception) per call."""

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

"""Settings loading and phone-number validation for the probe kit."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(ValueError):
    """Raised when configuration or a phone number is invalid."""


_US_E164 = re.compile(r"^\+1[2-9]\d{9}$")
_REQUIRED = (
    "VAPI_API_KEY",
    "VAPI_PHONE_NUMBER_ID",
    "VAPI_WEBHOOK_SECRET",
    "PROBE_PUBLIC_URL",
    "PROBE_RECEPTIONIST_NUMBERS",
    "PROBE_OPERATOR_NUMBER",
)


@dataclass(frozen=True)
class Settings:
    api_key: str
    phone_number_id: str
    webhook_secret: str
    public_url: str
    receptionist_numbers: tuple[str, ...]
    operator_number: str
    model: str = "gpt-5.4-mini"
    spend_cap_usd: float = 20.0
    api_base: str = "https://api.vapi.ai"

    @property
    def allowed_numbers(self) -> frozenset[str]:
        """Every number the kit may call: receptionists plus the operator."""
        return frozenset(self.receptionist_numbers) | {self.operator_number}


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines, skipping comments and blanks, stripping quotes."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key.strip()] = value
    return result


def validate_us_number(number: str) -> str:
    """Return the number if it is US E.164 and not an emergency number."""
    digits = number[2:] if number.startswith("+1") else number
    if digits.startswith("911") or number in ("112", "999"):
        raise ConfigError(f"{number!r} is an emergency number and is blocked")
    if not _US_E164.match(number):
        raise ConfigError(f"{number!r} is not a US E.164 number (+1 and 10 digits)")
    return number


def load_settings(env: Mapping[str, str]) -> Settings:
    """Build Settings from env-style values, naming every missing variable."""
    missing = [name for name in _REQUIRED if not env.get(name, "").strip()]
    if missing:
        raise ConfigError("Missing required variables: " + ", ".join(missing))
    receptionists = tuple(
        validate_us_number(part.strip())
        for part in env["PROBE_RECEPTIONIST_NUMBERS"].split(",")
        if part.strip()
    )
    try:
        cap = float(env.get("PROBE_SPEND_CAP_USD") or 20.0)
    except ValueError as exc:
        raise ConfigError("PROBE_SPEND_CAP_USD must be a number") from exc
    return Settings(
        api_key=env["VAPI_API_KEY"],
        phone_number_id=env["VAPI_PHONE_NUMBER_ID"],
        webhook_secret=env["VAPI_WEBHOOK_SECRET"],
        public_url=env["PROBE_PUBLIC_URL"].rstrip("/"),
        receptionist_numbers=receptionists,
        operator_number=validate_us_number(env["PROBE_OPERATOR_NUMBER"].strip()),
        model=env.get("VAPI_MODEL") or "gpt-5.4-mini",
        spend_cap_usd=cap,
    )

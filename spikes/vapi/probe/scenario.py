"""Scenario definitions loaded from scenarios/*.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from probe.config import ConfigError


@dataclass(frozen=True)
class ProfileField:
    """One fact about the user and whether the agent may volunteer it."""

    name: str
    value: str
    share: Literal["share", "ask"]


@dataclass(frozen=True)
class Scenario:
    """One test-call scenario."""

    id: str
    title: str
    business_name: str
    user_name: str
    goal: str
    opening_request: str
    profile: tuple[ProfileField, ...]
    constraints: tuple[str, ...]
    fallbacks: tuple[str, ...]
    never_agree: tuple[str, ...]
    probes: tuple[int, ...]

    @property
    def ask_first_fields(self) -> tuple[str, ...]:
        """Names of profile fields the agent must ask the user before sharing."""
        return tuple(f.name for f in self.profile if f.share == "ask")


_STR_KEYS = ("id", "title", "business_name", "user_name", "goal", "opening_request")
_LIST_KEYS = ("constraints", "fallbacks", "never_agree", "probes")


def load_scenario(path: Path) -> Scenario:
    """Load a scenario file; raise ConfigError on bad or missing content."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read scenario {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"scenario {path} must be a JSON object")
    missing = [k for k in (*_STR_KEYS, "profile", *_LIST_KEYS) if k not in data]
    if missing:
        raise ConfigError(f"scenario {path} is missing keys: {', '.join(missing)}")
    profile = []
    for item in data["profile"]:
        try:
            field = ProfileField(item["name"], item["value"], item["share"])
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"scenario {path} has a malformed profile entry: {item!r}") from exc
        if field.share not in ("share", "ask"):
            raise ConfigError(
                f"scenario {path}: profile field {field.name!r} has share={field.share!r}; "
                "use 'share' or 'ask'"
            )
        profile.append(field)
    return Scenario(
        **{k: data[k] for k in _STR_KEYS},
        profile=tuple(profile),
        **{k: tuple(data[k]) for k in _LIST_KEYS},
    )

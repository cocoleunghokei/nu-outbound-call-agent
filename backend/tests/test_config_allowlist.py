"""CALL_DESTINATION_ALLOWLIST is normalised and validated when the settings load."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_allowlist_is_empty_by_default_and_normalised_when_set() -> None:
    assert Settings().call_destination_allowlist == []
    settings = Settings(call_destination_allowlist=["(617) 555-0142", "+1 415 555 0123"])
    assert settings.call_destination_allowlist == ["+16175550142", "+14155550123"]


def test_allowlist_is_read_as_a_json_list_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALL_DESTINATION_ALLOWLIST", '["617-555-0142"]')
    assert Settings().call_destination_allowlist == ["+16175550142"]


def test_an_entry_that_is_not_a_dialable_us_number_fails_at_startup() -> None:
    with pytest.raises(ValidationError, match="CALL_DESTINATION_ALLOWLIST entry '911'"):
        Settings(call_destination_allowlist=["911"])

"""Tests for app/briefs that need no database: phone rules, readiness, request validation."""

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.briefs.phone import PhoneError, check_phone
from app.briefs.readiness import check_readiness
from app.briefs.router import validate_brief
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.voice.prompt_safety import Snapshot
from tests.test_auth import ISSUER, SECRET, bearer, make_token

EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "brief.v1.valid.json"

# Fake profile (AGENTS.md: fake personal data only).
PROFILE = Snapshot.from_rows(
    [
        ("calling_name", "Alex", "share_freely"),
        ("full_name", "Alex Chen", "share_freely"),
        ("phone", "+16175550142", "share_freely"),
        ("date_of_birth", "2004-03-15", "ask_first"),
    ]
)


def example_brief(**overrides: Any) -> dict[str, Any]:
    """The contract's own valid example, so these tests follow brief.v1 as it changes."""
    brief: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    brief.update(copy.deepcopy(overrides))
    return brief


# ---------------------------------------------------------------- phone (PC-7)


@pytest.mark.parametrize(
    ("raw", "e164"),
    [
        ("6175550123", "+16175550123"),
        ("(617) 555-0123", "+16175550123"),
        ("617.555.0123", "+16175550123"),
        ("1 617 555 0123", "+16175550123"),
        ("+1 (617) 555-0123", "+16175550123"),
        ("+16175550123", "+16175550123"),
        ("212–555–0199", "+12125550199"),  # en dashes, pasted from a website
        ("787 555 0123", "+17875550123"),  # Puerto Rico (US territory)
        ("1-800-555-0199", "+18005550199"),  # toll-free business line
    ],
)
def test_phone_accepts_us_numbers(raw: str, e164: str) -> None:
    result = check_phone(raw)
    assert result.ok and result.e164 == e164 and result.message == ""


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("911", "emergency"),
        (" 9-1-1 ", "emergency"),
        ("112", "emergency"),
        ("999", "emergency"),
        ("311", "short_code"),
        ("988", "invalid"),
        ("416 555 0123", "not_us"),  # Toronto: +1 but not US
        ("+1 604 555 0123", "not_us"),  # Vancouver
        ("876 555 0123", "not_us"),  # Jamaica
        ("+44 20 7946 0958", "not_us"),
        ("011 44 20 7946 0958", "not_us"),
        ("1-900-555-0123", "premium"),
        ("500 555 0123", "personal"),
        ("710 555 0123", "personal"),
        ("617555012", "invalid"),
        ("1-800-FLOWERS", "invalid"),
        ("911 555 0123", "invalid"),  # N11 area code
        ("617 911 0123", "invalid"),  # N11 exchange
        ("017 555 0123", "invalid"),
        ("617 155 0123", "invalid"),
    ],
)
def test_phone_rejects(raw: str, reason: PhoneError) -> None:
    result = check_phone(raw)
    assert not result.ok
    assert result.reason == reason
    assert result.message


def test_phone_rules_match_the_frontend_messages() -> None:
    """The chat (frontend/src/precall/phone.ts) shows the same sentence the API returns."""
    assert check_phone("911").message.startswith("We can't call emergency numbers.")
    assert check_phone("416 555 0123").message == "Only US phone numbers are supported for now."


# ---------------------------------------------------------------- readiness


def test_contract_example_is_ready_with_the_exact_opening_line() -> None:
    readiness = check_readiness(example_brief(), PROFILE)
    assert readiness.ready
    assert readiness.missing_fields == []
    assert readiness.opening_line == (
        "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. "
        "Book a table for 4 under Alex."
    )


def test_missing_calling_name_blocks_start() -> None:
    no_name = Snapshot.from_rows([("phone", "+16175550142", "share_freely")])
    readiness = check_readiness(example_brief(), no_name)
    assert not readiness.ready
    assert readiness.missing_fields == ["calling_name"]
    assert readiness.opening_line is None


def test_every_gap_is_reported_at_once() -> None:
    brief = example_brief(
        task_line="Book for {party_size} on {day} at {time} for {who}.",
        task_vars={"party_size": {"literal": "4"}, "who": {"field_ref": "email"}},
    )
    readiness = check_readiness(brief, PROFILE)
    assert readiness.missing_fields == ["day", "time", "email"]


def test_an_ask_first_field_ref_is_never_resolved() -> None:
    brief = example_brief(
        task_line="Born {dob}.", task_vars={"dob": {"field_ref": "date_of_birth"}}
    )
    readiness = check_readiness(brief, PROFILE)
    assert readiness.missing_fields == ["date_of_birth"]
    assert readiness.opening_line is None


def test_an_ask_first_value_typed_into_the_brief_is_caught() -> None:
    brief = example_brief(constraints=[{"text": "Born 2004-03-15, say if asked"}])
    readiness = check_readiness(brief, PROFILE)
    assert readiness.missing_fields == ["date_of_birth"]
    assert not readiness.ready


def test_no_fallback_is_never_missing() -> None:
    """PC-5 only prompts for a fallback; an empty list must not block Start."""
    readiness = check_readiness(example_brief(fallbacks=[]), PROFILE)
    assert readiness.ready


# ---------------------------------------------------------------- validate_brief


def test_validate_brief_normalises_the_number() -> None:
    assert validate_brief(example_brief(phone_e164="+14155550123"))["phone_e164"] == "+14155550123"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"goal": ""}, "invalid_brief"),
        ({"task_line": "x" * 121}, "invalid_brief"),
        ({"surprise": 1}, "invalid_brief"),
        ({"phone_e164": "+14165550123"}, "invalid_phone"),  # Toronto
        ({"phone_e164": "+19005550123"}, "invalid_phone"),  # premium
    ],
)
def test_validate_brief_rejects(overrides: dict[str, Any], code: str) -> None:
    with pytest.raises(ApiError) as excinfo:
        validate_brief(example_brief(**overrides))
    assert excinfo.value.code == code
    assert excinfo.value.status == 422


def test_validate_brief_rejects_a_missing_field() -> None:
    brief = example_brief()
    del brief["goal"]
    with pytest.raises(ApiError) as excinfo:
        validate_brief(brief)
    assert excinfo.value.code == "invalid_brief"
    assert "goal" in excinfo.value.message


# ---------------------------------------------------------------- HTTP, without a database


@pytest.fixture
def client() -> TestClient:
    settings = Settings(supabase_jwt_secret=SECRET, supabase_jwt_issuer=ISSUER)  # type: ignore[arg-type]
    return TestClient(create_app(settings))


def headers(key: bool = True) -> dict[str, str]:
    h = bearer(make_token())
    if key:
        h["Idempotency-Key"] = str(uuid.uuid4())
    return h


def test_routes_are_mounted_by_register(client: TestClient) -> None:
    """register(reg) mounts every route: unauthenticated calls get 401, never 404."""
    brief_url = f"/briefs/{uuid.uuid4()}"
    for method, url in [("POST", "/briefs"), ("GET", brief_url), ("PATCH", brief_url)]:
        response = client.request(method, url, json=example_brief())
        assert response.status_code == 401, (method, url, response.status_code)


def test_create_requires_auth(client: TestClient) -> None:
    response = client.post("/briefs", json=example_brief())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_create_requires_an_idempotency_key(client: TestClient) -> None:
    response = client.post("/briefs", json=example_brief(), headers=headers(key=False))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


def test_create_rejects_a_bad_number_before_touching_the_database(client: TestClient) -> None:
    response = client.post(
        "/briefs", json=example_brief(phone_e164="+14165550123"), headers=headers()
    )
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid_phone",
        "message": "Only US phone numbers are supported for now.",
        "retryable": False,
    }


def test_create_rejects_an_invalid_brief(client: TestClient) -> None:
    response = client.post("/briefs", json=example_brief(goal=""), headers=headers())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_brief"


def test_valid_requests_reach_the_database_step(client: TestClient) -> None:
    """Without DATABASE_URL a valid request gets the shared 503, so validation passed."""
    created = client.post("/briefs", json=example_brief(), headers=headers())
    assert created.status_code == 503
    assert created.json()["error"]["code"] == "database_unavailable"
    read = client.get(f"/briefs/{uuid.uuid4()}", headers=headers(key=False))
    assert read.status_code == 503
    replaced = client.patch(f"/briefs/{uuid.uuid4()}", json=example_brief(), headers=headers())
    assert replaced.status_code == 503


def test_replace_requires_an_idempotency_key(client: TestClient) -> None:
    response = client.patch(
        f"/briefs/{uuid.uuid4()}", json=example_brief(), headers=headers(key=False)
    )
    assert response.status_code == 400


def test_a_bad_brief_id_is_rejected(client: TestClient) -> None:
    response = client.get("/briefs/not-a-uuid", headers=headers(key=False))
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/briefs"), ("GET", "/briefs/{id}"), ("PATCH", "/briefs/{id}")],
)
def test_browser_preflight_allows_every_brief_route(method: str, path: str) -> None:
    """Regression: the shared CORS setup allows PATCH but not PUT, so the browser must be able
    to send every method the brief routes use, with the Idempotency-Key header."""
    origin = "https://preview-123.vercel.app"
    settings = Settings(cors_origin_regex=r"https://.*\.vercel\.app")
    response = TestClient(create_app(settings)).options(
        path.replace("{id}", str(uuid.uuid4())),
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "Authorization, Content-Type, Idempotency-Key",
        },
    )
    assert response.status_code == 200, (method, response.text)
    assert method in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] == origin

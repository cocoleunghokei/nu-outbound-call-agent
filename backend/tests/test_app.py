"""Tests for the app skeleton: error shape, CORS, package discovery."""

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.config import Settings
from app.errors import ApiError
from app.main import create_app

VERCEL_REGEX = r"https://.*\.vercel\.app"


def make_app(packages_root: Path | None = None) -> FastAPI:
    settings = Settings(cors_origin_regex=VERCEL_REGEX)
    if packages_root is None:
        app = create_app(settings)
    else:
        app = create_app(settings, packages_root=packages_root)

    class Body(BaseModel):
        n: int

    router = APIRouter()

    @router.get("/boom-api")
    def boom_api() -> None:
        raise ApiError("conflict_thing", 409, "Nope", retryable=False)

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    @router.post("/validate")
    def validate(body: Body) -> dict[str, int]:
        return {"n": body.n}

    app.include_router(router)
    return app


def test_health_ok() -> None:
    client = TestClient(make_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_error_shape() -> None:
    response = TestClient(make_app()).get("/boom-api")
    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "conflict_thing", "message": "Nope", "retryable": False}
    }


def test_validation_error_uses_shape() -> None:
    response = TestClient(make_app()).post("/validate", json={"n": "x"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["retryable"] is False
    assert isinstance(error["message"], str)


def test_404_and_405_use_shape() -> None:
    client = TestClient(make_app())
    missing = client.get("/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    wrong_method = client.post("/health")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"


def test_discovered_package_router_is_mounted(tmp_path: Path) -> None:
    package = tmp_path / "demo_owner"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/x')\n"
        "def x() -> dict[str, str]:\n"
        "    return {'ok': 'yes'}\n"
        "def register(reg) -> None:\n"
        "    reg.add_router(router)\n"
    )
    with TestClient(make_app(packages_root=tmp_path)) as client:
        assert client.get("/x").status_code == 200


def test_startup_hook_runs(tmp_path: Path) -> None:
    package = tmp_path / "hook_owner"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from fastapi import APIRouter\n"
        "state = {'started': False}\n"
        "router = APIRouter()\n"
        "@router.get('/started')\n"
        "def started() -> dict[str, bool]:\n"
        "    return state\n"
        "async def hook() -> None:\n"
        "    state['started'] = True\n"
        "def register(reg) -> None:\n"
        "    reg.add_router(router)\n"
        "    reg.on_startup(hook)\n"
    )
    with TestClient(make_app(packages_root=tmp_path)) as client:
        assert client.get("/started").json() == {"started": True}


def test_unhandled_error_hides_details() -> None:
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal", "message": "Internal server error", "retryable": True}
    }
    assert "secret internals" not in response.text


def test_cors_preflight_allows_vercel_origin_and_idempotency_header() -> None:
    origin = "https://preview-123.vercel.app"
    response = TestClient(make_app()).options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key, Authorization, Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "idempotency-key" in allowed
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", ["https://evil.example.test", "http://localhost:9999"])
def test_cors_rejects_unknown_origin(origin: str) -> None:
    response = TestClient(make_app()).options(
        "/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_unhandled_error_response_carries_cors_header() -> None:
    origin = "https://preview-123.vercel.app"
    client = TestClient(make_app(), raise_server_exceptions=False)
    response = client.get("/boom", headers={"Origin": origin})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == origin
    assert response.json()["error"] == {
        "code": "internal",
        "message": "Internal server error",
        "retryable": True,
    }


def test_env_example_loads_with_defaults() -> None:
    example = Path(__file__).parent.parent / ".env.example"
    settings = Settings(_env_file=example)
    assert settings.voice_platform == "fake"
    assert settings.cors_origins == ["http://localhost:5173"]
    assert settings.vapi_ip_allowlist is False
    assert settings.supabase_jwt_secret is None

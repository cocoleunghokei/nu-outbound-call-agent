"""Tests for the app skeleton: error shape, CORS, package discovery."""

import asyncio
import logging
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app import main as main_module
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.registry import AppContext, Registries

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
        "async def hook(ctx) -> None:\n"
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


# --- owner hooks: AppContext, workers, shutdown -------------------------------------------


def _write_package(root: Path, name: str, source: str) -> None:
    package = root / name
    package.mkdir()
    (package / "__init__.py").write_text(source)


def test_startup_hook_receives_app_context(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        "ctx_owner",
        "seen = {}\n"
        "async def hook(ctx) -> None:\n"
        "    seen['ctx'] = ctx\n"
        "def register(reg) -> None:\n"
        "    reg.on_startup(hook)\n",
    )
    app = make_app(packages_root=tmp_path)
    with TestClient(app):
        ctx = sys.modules["_discovered_ctx_owner"].seen["ctx"]
        assert isinstance(ctx, AppContext)
        assert ctx.settings is app.state.settings
        assert ctx.registries is app.state.registries
        assert ctx.pool is None  # no database_url in this test


class _FakePool:
    """Stands in for AsyncConnectionPool and records when it is opened and closed."""

    def __init__(self, journal: list[str]) -> None:
        self.journal = journal

    async def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        self.journal.append("pool.open")

    async def close(self) -> None:
        self.journal.append("pool.close")


def test_worker_runs_and_is_cancelled_before_shutdown_hooks_and_pool_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal: list[str] = []
    pool = _FakePool(journal)
    monkeypatch.setattr(main_module, "create_pool", lambda url: pool)

    async def no_ingress_startup(pool: object, tools: object) -> None:
        journal.append("ingress.startup")

    monkeypatch.setattr(main_module.vapi_webhooks, "run_ingress_startup", no_ingress_startup)
    started = asyncio.Event()

    async def worker(ctx: AppContext) -> None:
        assert ctx.pool is pool
        journal.append("worker.start")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            journal.append("worker.cancelled")
            raise

    async def on_shutdown(ctx: AppContext) -> None:
        assert ctx.pool is pool
        journal.append("shutdown.hook")

    def register_hooks(reg: Registries) -> None:
        reg.add_worker(worker)
        reg.on_shutdown(on_shutdown)

    app = create_app(Settings(database_url="postgresql://fake.example.test/db"))
    register_hooks(app.state.registries)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(started.wait(), 2)
            journal.append("serving")

    asyncio.run(scenario())
    assert journal == [
        "pool.open",
        "ingress.startup",
        "worker.start",
        "serving",
        "worker.cancelled",
        "shutdown.hook",
        "pool.close",
    ]


def test_crashing_worker_is_logged_and_app_keeps_serving(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = make_app()

    async def crashing(ctx: AppContext) -> None:
        raise RuntimeError("worker blew up")

    app.state.registries.add_worker(crashing)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        with TestClient(app) as client:
            for _ in range(20):
                if any("worker blew up" in (r.exc_text or "") for r in caplog.records):
                    break
                client.get("/health")
            assert client.get("/health").status_code == 200
    crash_logs = [r for r in caplog.records if "crashing" in r.getMessage()]
    assert crash_logs, [r.getMessage() for r in caplog.records]

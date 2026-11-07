"""FastAPI application factory. Owner packages plug in through register(reg)."""

import asyncio
import importlib
import importlib.util
import logging
import pkgutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app as app_package
from app.config import Settings
from app.db import create_pool
from app.errors import UnhandledErrorMiddleware, install_error_handlers
from app.registry import AppContext, LifecycleHook, Registries
from app.routers import me
from app.secrets_registry import get_secret_registry, install_redacting_formatter
from app.webhooks import vapi as vapi_webhooks

DEFAULT_PACKAGES_ROOT = Path(next(iter(app_package.__path__)))

logger = logging.getLogger(__name__)


async def _ingress_startup(ctx: AppContext) -> None:
    """Internal startup hook: load call_secrets and run dispatch_pending (needs the pool)."""
    if ctx.pool is not None:
        await vapi_webhooks.run_ingress_startup(ctx.pool, ctx.registries.tools)


def _hook_name(fn: LifecycleHook) -> str:
    return getattr(fn, "__qualname__", None) or repr(fn)


async def _run_worker(fn: LifecycleHook, ctx: AppContext) -> None:
    """Run one worker; log (through the redacting root handlers) instead of crashing."""
    try:
        await fn(ctx)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("worker %s crashed and stopped", _hook_name(fn))


async def _stop_workers(tasks: list[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("worker task failed while stopping")


async def _run_shutdown_hooks(reg: Registries, ctx: AppContext) -> None:
    for hook in reg.shutdown_hooks:
        try:
            await hook(ctx)
        except Exception:
            logger.exception("shutdown hook %s failed", _hook_name(hook))


def _load_packages(reg: Registries, packages_root: Path) -> None:
    """Import every subpackage under packages_root and call its register(reg), if any."""
    for info in pkgutil.iter_modules([str(packages_root)]):
        if not info.ispkg:
            continue
        if packages_root == DEFAULT_PACKAGES_ROOT:
            module = importlib.import_module(f"app.{info.name}")
        else:
            module = _import_from_path(info.name, packages_root / info.name)
        register = getattr(module, "register", None)
        if callable(register):
            register(reg)


def _import_from_path(name: str, package_dir: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_discovered_{name}",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load package at {package_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_app(settings: Settings | None = None, packages_root: Path | None = None) -> FastAPI:
    """Build the app. packages_root is injectable so tests can point discovery elsewhere."""
    settings = settings or Settings()
    reg = Registries()
    reg.on_startup(_ingress_startup)  # first, so owners' hooks run after pending dispatch
    _load_packages(reg, packages_root or DEFAULT_PACKAGES_ROOT)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        install_redacting_formatter()
        get_secret_registry().add_settings(settings)
        # No database_url (non-db tests): no pool, and DB routes answer 503.
        pool = create_pool(settings.database_url) if settings.database_url else None
        app.state.pool = pool
        ctx = AppContext(pool=pool, settings=settings, registries=reg)
        app.state.context = ctx
        workers: list[asyncio.Task[None]] = []
        if pool is not None:
            await pool.open(wait=True, timeout=10)
        try:
            for hook in reg.startup_hooks:
                await hook(ctx)
            for fn in reg.workers:
                workers.append(
                    asyncio.create_task(_run_worker(fn, ctx), name=f"worker:{_hook_name(fn)}")
                )
            yield
        finally:
            await _stop_workers(workers)
            await _run_shutdown_hooks(reg, ctx)
            if pool is not None:
                await pool.close()

    app = FastAPI(title="Call agent backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.registries = reg
    install_error_handlers(app)
    # Added first so it sits inside CORS; CORS then decorates its 500 responses.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        allow_credentials=False,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(me.router)
    app.include_router(vapi_webhooks.router)
    for router in reg.routers:
        app.include_router(router)
    return app


app = create_app()

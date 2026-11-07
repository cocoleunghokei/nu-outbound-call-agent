"""FastAPI application factory. Owner packages plug in through register(reg)."""

import importlib
import importlib.util
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
from app.errors import install_error_handlers
from app.registry import Registries

DEFAULT_PACKAGES_ROOT = Path(next(iter(app_package.__path__)))


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
    _load_packages(reg, packages_root or DEFAULT_PACKAGES_ROOT)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        for hook in reg.startup_hooks:
            await hook()
        yield

    app = FastAPI(title="Call agent backend", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
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

    for router in reg.routers:
        app.include_router(router)
    return app


app = create_app()

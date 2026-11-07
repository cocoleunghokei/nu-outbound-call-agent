"""Plug-in points that owner packages use to attach themselves to the app."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.webhooks.registry import EventRegistry, ToolRegistry


@dataclass(frozen=True)
class AppContext:
    """What startup hooks, workers and shutdown hooks receive once the app is starting.

    ``pool`` is the shared database pool, or None when no ``database_url`` is configured
    (non-db tests). ``registries`` is the same ``Registries`` the package registered into,
    so a sweeper can call ``dispatch_pending(ctx.pool, tools=ctx.registries.tools)``.
    """

    pool: AsyncConnectionPool[Any] | None
    settings: Settings
    registries: "Registries"


LifecycleHook = Callable[[AppContext], Awaitable[None]]


class Registries:
    """Collects routers, lifecycle hooks, workers and webhook handlers from each package.

    ``tools`` and ``events`` are the Vapi webhook handler registries (app/webhooks/registry.py):
    ``reg.tools.register_tool_handler(name, fn)`` and
    ``reg.events.register_event_handler(vapi_type, fn)``.

    Lifecycle, in order: the pool opens, startup hooks run one by one, workers start as
    background tasks, the app serves. On shutdown the workers are cancelled and awaited, the
    shutdown hooks run, and the pool closes.
    """

    def __init__(self) -> None:
        self.routers: list[APIRouter] = []
        self.startup_hooks: list[LifecycleHook] = []
        self.shutdown_hooks: list[LifecycleHook] = []
        self.workers: list[LifecycleHook] = []
        self.tools = ToolRegistry()
        self.events = EventRegistry()

    def add_router(self, router: APIRouter) -> None:
        """Mount a router on the app."""
        self.routers.append(router)

    def on_startup(self, fn: LifecycleHook) -> None:
        """Run ``fn(ctx)`` during startup, after the pool opens. It must return, not loop:
        startup waits for it, and an exception stops the app from starting."""
        self.startup_hooks.append(fn)

    def on_shutdown(self, fn: LifecycleHook) -> None:
        """Run ``fn(ctx)`` during shutdown, after the workers stop and before the pool closes.
        An exception is logged and the remaining hooks still run."""
        self.shutdown_hooks.append(fn)

    def add_worker(self, fn: LifecycleHook) -> None:
        """Run ``fn(ctx)`` as a background task for the life of the app (a loop such as the
        sweeper or the outbox worker). It starts after the startup hooks and is cancelled on
        shutdown, so let ``asyncio.CancelledError`` propagate. A worker that raises is logged
        and stops; the app keeps serving."""
        self.workers.append(fn)

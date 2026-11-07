"""Plug-in points that owner packages use to attach themselves to the app."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter


class Registries:
    """Collects routers and startup hooks from each owner package's register()."""

    def __init__(self) -> None:
        self.routers: list[APIRouter] = []
        self.startup_hooks: list[Callable[[], Awaitable[None]]] = []

    def add_router(self, router: APIRouter) -> None:
        """Mount a router on the app."""
        self.routers.append(router)

    def on_startup(self, fn: Callable[[], Awaitable[None]]) -> None:
        """Run fn during application startup."""
        self.startup_hooks.append(fn)

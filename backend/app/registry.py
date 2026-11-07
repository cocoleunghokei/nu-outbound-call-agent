"""Plug-in points that owner packages use to attach themselves to the app."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter

from app.webhooks.registry import EventRegistry, ToolRegistry


class Registries:
    """Collects routers, startup hooks and webhook handlers from each package's register().

    ``tools`` and ``events`` are the Vapi webhook handler registries (app/webhooks/registry.py):
    ``reg.tools.register_tool_handler(name, fn)`` and
    ``reg.events.register_event_handler(vapi_type, fn)``.
    """

    def __init__(self) -> None:
        self.routers: list[APIRouter] = []
        self.startup_hooks: list[Callable[[], Awaitable[None]]] = []
        self.tools = ToolRegistry()
        self.events = EventRegistry()

    def add_router(self, router: APIRouter) -> None:
        """Mount a router on the app."""
        self.routers.append(router)

    def on_startup(self, fn: Callable[[], Awaitable[None]]) -> None:
        """Run fn during application startup."""
        self.startup_hooks.append(fn)

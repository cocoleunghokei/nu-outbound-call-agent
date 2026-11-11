"""Lotto's package: the pre-call brief (PC-1 to PC-7).

Plugs in through ``register(reg)``; ``main.py`` finds it automatically. For Coco's
``POST /calls``: ``app.briefs.store.brief_readiness`` and ``app.briefs.phone.check_phone``.
"""

from app.briefs.router import router
from app.registry import AppContext, Registries
from app.secrets_registry import get_secret_registry


async def redact_llm_key(ctx: AppContext) -> None:
    """Add the DeepSeek key to the process-wide secret registry, so no log line shows it.

    The shared ``add_settings`` covers the Vapi and JWT secrets; this package owns the LLM
    key, so it registers it itself through the registry's public ``add`` (empty is ignored).
    """
    get_secret_registry().add(ctx.settings.deepseek_api_key.get_secret_value())


def register(reg: Registries) -> None:
    """Mount the brief routes and register the LLM key for log redaction."""
    reg.add_router(router)
    reg.on_startup(redact_llm_key)

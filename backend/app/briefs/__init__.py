"""Lotto's package: the pre-call brief (PC-1 to PC-7).

Plugs in through ``register(reg)``; ``main.py`` finds it automatically. For Coco's
``POST /calls``: ``app.briefs.store.brief_readiness`` and ``app.briefs.phone.check_phone``.
"""

from app.briefs.router import router
from app.registry import Registries


def register(reg: Registries) -> None:
    """Mount the brief routes."""
    reg.add_router(router)

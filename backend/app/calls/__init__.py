"""During call (Coco): the ask manager and the control-lane outbox (DC-6 to DC-9).

Plugs into the app through ``register(reg)``, which ``create_app`` finds automatically:

- ``ask_user`` tool handler: opens a ping (``app/calls/ask.py``).
- ``POST /calls/{call_id}/pings/{ping_id}/reply``: the user's answer (``app/calls/reply.py``).
- ``sweeper`` worker: expires unanswered pings and runs pending tool calls.
- ``control_worker`` worker and ``recover_outbox`` startup hook: deliver queued replies,
  timeouts and "still checking" lines to the live call (``app/calls/outbox.py``).

Pause / Resume / End, the status machine and ``report_decision`` come in the next PR.
"""

from app.calls.ask import handle_ask_user
from app.calls.outbox import control_worker, recover_outbox
from app.calls.reply import router
from app.calls.sweeper import sweeper
from app.registry import Registries


def register(reg: Registries) -> None:
    """Attach the during-call package to the app."""
    reg.tools.register_tool_handler("ask_user", handle_ask_user)
    reg.add_router(router)
    reg.on_startup(recover_outbox)
    reg.add_worker(sweeper)
    reg.add_worker(control_worker)

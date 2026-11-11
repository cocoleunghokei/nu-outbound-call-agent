"""During call (Coco): the ask manager, Pause / Resume / End, the status machine and the
control-lane outbox (DC-4, DC-6 to DC-9, DC-11, DC-14).

Plugs into the app through ``register(reg)``, which ``create_app`` finds automatically:

- ``ask_user`` tool handler: opens a ping (``app/calls/ask.py``).
- ``POST /calls/{call_id}/pings/{ping_id}/reply``: the user's answer (``app/calls/reply.py``).
- ``report_decision`` tool handler: logs what the agent did on its own (``decisions.py``).
- ``POST /calls/{call_id}/commands``: Pause, Resume, End call (``commands.py``).
- ``status-update`` event handler: Vapi's call status -> ours (``status.py``).
- ``speech-update`` event handler: the quiet rule that times the mute on Pause (``speech.py``).
- ``sweeper`` worker: expires unanswered pings, ends calls paused past the cap, runs pending
  tool calls.
- ``control_worker`` worker and ``recover_outbox`` startup hook: deliver queued replies,
  timeouts and "still checking" lines to the live call (``app/calls/outbox.py``).

- ``POST /calls``: start a reviewed brief as a real call (``app/calls/create.py``).
"""

from app.calls import commands, create, reply
from app.calls.ask import handle_ask_user
from app.calls.decisions import handle_report_decision
from app.calls.outbox import control_worker, recover_outbox
from app.calls.speech import handle_speech_update
from app.calls.status import handle_status_update
from app.calls.sweeper import sweeper
from app.registry import Registries


def register(reg: Registries) -> None:
    """Attach the during-call package to the app."""
    reg.tools.register_tool_handler("ask_user", handle_ask_user)
    reg.tools.register_tool_handler("report_decision", handle_report_decision)
    reg.events.register_event_handler("status-update", handle_status_update)
    reg.events.register_event_handler("speech-update", handle_speech_update)
    reg.add_router(create.router)
    reg.add_router(reply.router)
    reg.add_router(commands.router)
    reg.on_startup(recover_outbox)
    reg.add_worker(sweeper)
    reg.add_worker(control_worker)

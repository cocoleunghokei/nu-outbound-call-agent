"""Harper's package: post-call processing (PO-1 to PO-6).

- ``report_outcome`` tool handler and ``end-of-call-report`` event handler (``handlers.py``)
- the ``jobs`` outbox lane worker: outcome and shared fields (``jobs.py``)
- the pure rules behind them (``decide.py``)
- the read API for the post-call screens (``history.py``)
"""

from app.postcall import history
from app.postcall.handlers import handle_end_of_call_report, handle_report_outcome
from app.postcall.jobs import postcall_worker
from app.registry import Registries


def register(reg: Registries) -> None:
    """Plug post-call into the app (found by ``create_app``)."""
    reg.tools.register_tool_handler("report_outcome", handle_report_outcome)
    reg.events.register_event_handler("end-of-call-report", handle_end_of_call_report)
    reg.add_worker(postcall_worker)
    reg.add_router(history.router)

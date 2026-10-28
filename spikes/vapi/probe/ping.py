"""Pure pause-and-ping state machine: decides which control messages to send when."""
from __future__ import annotations

from dataclasses import dataclass

from probe.control import add_system_message, say

FILLER_INTERVAL_S = 15
TIMEOUT_S = 45
HOLD_LINE_PREFIX = "Let me check with"
FILLER_LINES = (
    "Thanks for holding, I'm just checking with {user}.",
    "Still here, {user} is looking at this now.",
    "Sorry for the wait, I'm waiting on {user}.",
)
REPLY_MARKER = " replied: "
TIMEOUT_MARKER = "did not reply within"


@dataclass
class PingView:
    """What the operator terminal shows for the open ping."""
    questions: list[str]
    reasons: list[str]
    suggested_replies: list[str]
    opened_at: float


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _str_list(v) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _extend(dst: list[str], src: list[str]) -> None:
    dst.extend(x for x in src if x not in dst)


class PingSession:
    """One call's ping state. Never sleeps, reads a clock or does I/O."""

    def __init__(self, user_name: str):
        self.user = user_name
        self.notices: list[str] = []
        self._view: PingView | None = None
        self._fillers_sent = 0
        self._last_close: str | None = None

    @property
    def is_open(self) -> bool:
        return self._view is not None

    @property
    def view(self) -> PingView | None:
        return self._view

    def on_ask(self, args: dict, now: float) -> list[dict]:
        """Open a ping (hold line) or merge into the open one (no messages)."""
        q, r = _str(args.get("question")), _str(args.get("reason"))
        s = _str_list(args.get("suggested_replies"))
        if self._view is not None:
            _extend(self._view.questions, [q])
            _extend(self._view.reasons, [r])
            _extend(self._view.suggested_replies, s)
            return []
        self._view = PingView([q], [r], list(dict.fromkeys(s)), now)
        self._fillers_sent = 0
        return [say(f"{HOLD_LINE_PREFIX} {self.user}, one moment.")]

    def tick(self, now: float) -> list[dict]:
        """Timeout at +45 s (wins), else at most one overdue filler."""
        if self._view is None:
            return []
        elapsed = now - self._view.opened_at
        if elapsed >= TIMEOUT_S:
            self._close("timeout")
            return [add_system_message(
                f"{self.user} {TIMEOUT_MARKER} {TIMEOUT_S} seconds. Apply the no-reply rule: "
                "if the latest offer matches a listed fallback, accept it and say which "
                "fallback it matched. Otherwise end the call politely, say you will confirm "
                f"with {self.user} and call back, and do not guess.", trigger_response=True)]
        due = min(int(elapsed // FILLER_INTERVAL_S), len(FILLER_LINES))
        if due > self._fillers_sent:
            line = FILLER_LINES[self._fillers_sent % len(FILLER_LINES)]
            self._fillers_sent += 1
            return [say(line.format(user=self.user))]
        return []

    def on_reply(self, text: str, now: float) -> list[dict]:
        """Inject the operator's reply into the call, or note why it was ignored."""
        if self._view is None:
            if self._last_close == "timeout":
                self.notices.append("Reply ignored: the ping timed out, nothing was sent.")
            else:
                self.notices.append("Reply ignored: no ping is open.")
            return []
        text = text.strip()
        if not text:
            self.notices.append("Reply ignored: empty. Type a reply and press Enter.")
            return []
        sug = self._view.suggested_replies
        if text in ("1", "2", "3", "4") and int(text) <= len(sug):
            text = sug[int(text) - 1]
        self._close("replied")
        return [add_system_message(f"{self.user}{REPLY_MARKER}{text}", trigger_response=True)]

    def _close(self, reason: str) -> None:
        self._view, self._last_close = None, reason

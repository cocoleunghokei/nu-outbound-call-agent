"""What one eval run records, so it can be checked, judged and reported without re-running.

A run is one case played from start to finish. Every event carries the script ``step`` it
belongs to: 0 is the opening line, N is the business line or system message at script step N
and the model's replies to it. Each record round-trips through ``to_json`` / ``from_json``
(one line of ``runs.jsonl``).
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EventKind = Literal["business", "system", "agent", "tool_call", "tool_result"]
TurnType = Literal["talk", "decision", "after_system"]


@dataclass(frozen=True)
class ToolCall:
    """One tool call the model made. ``args`` is None when its arguments weren't valid JSON."""

    call_id: str
    name: str
    args: dict[str, Any] | None
    raw_args: str


@dataclass(frozen=True)
class ModelReply:
    """One model response: what it said, the tools it called, and how long it took.

    Times are seconds from the request being sent. ``first_token_s`` is the first spoken
    token (None if it said nothing); ``done_s`` is when the stream ended, which is also when
    any tool call was complete.
    """

    text: str
    tool_calls: list[ToolCall]
    first_token_s: float | None
    done_s: float
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    """One entry in a run's transcript, in order."""

    step: int
    kind: EventKind
    text: str = ""
    tool: str = ""
    args: dict[str, Any] | None = None
    call_id: str = ""
    system_kind: str = ""


@dataclass(frozen=True)
class Turn:
    """The silence the business heard after one script step (see CASES.md, latency)."""

    step: int
    type: TurnType
    silence_s: float | None
    tool_before_speaking: bool
    time_to_ping_s: float | None


@dataclass
class RunRecord:
    """Everything about one run of one case."""

    case_id: str
    run: int
    model: str
    temperature: float | None
    started_at: str
    events: list[Event] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    usage: list[dict[str, int]] = field(default_factory=list)
    stopped: str = ""
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        """The record as plain JSON types."""
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RunRecord":
        """Read a record written by ``to_json``."""
        return cls(
            case_id=data["case_id"],
            run=data["run"],
            model=data["model"],
            temperature=data["temperature"],
            started_at=data["started_at"],
            events=[Event(**e) for e in data["events"]],
            turns=[Turn(**t) for t in data["turns"]],
            usage=list(data["usage"]),
            stopped=data["stopped"],
            error=data["error"],
        )


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict for one run. ``pending`` means the judge still has to decide."""

    case_id: str
    run: int
    check: str
    text: str
    level: Literal["hard", "soft"]
    by: str
    verdict: Literal["pass", "fail", "unclear", "pending", "n/a"]
    evidence: str = ""
    guardrail: bool = False
    accuracy: bool = False

    def to_json(self) -> dict[str, Any]:
        """The result as plain JSON types (one line of ``script.jsonl``)."""
        return asdict(self)

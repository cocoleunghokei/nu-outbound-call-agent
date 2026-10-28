"""Append-only JSONL event log and per-call run metadata."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Event:
    """One logged event; `t` is local epoch seconds."""
    t: float
    kind: str
    payload: dict


class EventLog:
    """Thread-safe JSONL writer."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.Lock()

    def append(self, kind: str, payload: dict) -> None:
        """Write one {"t", "kind", "payload"} line, creating the parent directory."""
        line = json.dumps({"t": self.clock(), "kind": kind, "payload": payload})
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def read_events(path: Path) -> list[Event]:
    """Read all events from a log, skipping blank lines."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(Event(d["t"], d["kind"], d["payload"]))
    return out


@dataclass
class RunMeta:
    """Per-call metadata saved next to the event log."""
    call_id: str
    scenario_id: str
    to: str
    control_url: str
    listen_url: str
    started_at: float
    cost: float | None = None


def save_meta(runs_dir: Path, meta: RunMeta) -> None:
    """Write runs/<call_id>.meta.json."""
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{meta.call_id}.meta.json").write_text(
        json.dumps(asdict(meta), indent=2), encoding="utf-8")


def load_meta(runs_dir: Path, call_id: str) -> RunMeta | None:
    """Load a call's metadata, or None if unknown."""
    path = Path(runs_dir) / f"{call_id}.meta.json"
    if not path.exists():
        return None
    return RunMeta(**json.loads(path.read_text(encoding="utf-8")))


def running_cost(runs_dir: Path) -> float:
    """Sum of `cost` across runs/*.meta.json; None counts as 0."""
    total = 0.0
    for p in Path(runs_dir).glob("*.meta.json"):
        total += json.loads(p.read_text(encoding="utf-8")).get("cost") or 0.0
    return total

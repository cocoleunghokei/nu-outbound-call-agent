"""Process-wide set of secret strings (Vapi capability URLs) that must never be logged.

Values are only ever added, never removed: a URL that leaked into a log line after its call
ended is still a secret. Task 7 adds startup loading and a redacting log formatter.
"""

import threading


class SecretRegistry:
    """A thread-safe, add-only set of secret strings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()

    def add(self, value: str) -> None:
        """Remember ``value`` as a secret. Empty strings are ignored."""
        if not value:
            return
        with self._lock:
            self._values.add(value)

    def all(self) -> frozenset[str]:
        """Return a snapshot of every secret added so far."""
        with self._lock:
            return frozenset(self._values)


_REGISTRY = SecretRegistry()


def get_secret_registry() -> SecretRegistry:
    """Return the process-wide registry."""
    return _REGISTRY

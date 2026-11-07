"""Process-wide set of secret strings that must never be logged, and the redacting formatter.

The registry holds the webhook secret, the Vapi API key, the JWT secret and every Vapi
capability URL (control and listen) seen since the process started. Values are only ever
added, never removed: a URL that leaks into a log line after its call ended is still a secret.
At startup ``load_call_secrets`` adds every URL still in ``call_secrets``;
``calls_lock.write_call_secrets`` and the webhook ingress add new ones as they arrive.

``RedactingFormatter`` scrubs the final formatted log line (message, exception text and stack)
against this registry. ``install_redacting_formatter`` puts it on the root handlers and on
uvicorn's handlers, so logs from the adapter, the outbox worker and the sweeper are covered
too, not only logs written inside a webhook request.
"""

import logging
import sys
import threading
from typing import Any

from psycopg import AsyncConnection

from app.config import Settings
from app.webhooks.redact import redact_text

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


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

    def add_settings(self, settings: Settings) -> None:
        """Add the webhook secret, the Vapi API key and the JWT secret, when they are set."""
        self.add(settings.vapi_webhook_secret.get_secret_value())
        self.add(settings.vapi_api_key.get_secret_value())
        if settings.supabase_jwt_secret is not None:
            self.add(settings.supabase_jwt_secret.get_secret_value())

    async def load_call_secrets(self, conn: AsyncConnection[Any]) -> int:
        """Add every control and listen URL stored in ``call_secrets``; return the row count."""
        found = await (
            await conn.execute("select control_url, listen_url from public.call_secrets")
        ).fetchall()
        for control_url, listen_url in found:
            self.add(control_url)
            self.add(listen_url)
        return len(found)


_REGISTRY = SecretRegistry()


def get_secret_registry() -> SecretRegistry:
    """Return the process-wide registry."""
    return _REGISTRY


class RedactingFormatter(logging.Formatter):
    """Formats a record (optionally with another formatter), then redacts the whole string.

    Redaction runs on the final string, so the message, its arguments, the exception text and
    the stack are all covered, whatever the call site passed in.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        *,
        inner: logging.Formatter | None = None,
        registry: SecretRegistry | None = None,
    ) -> None:
        super().__init__(fmt, datefmt)
        self.inner = inner
        self._registry = registry or get_secret_registry()

    def format(self, record: logging.LogRecord) -> str:
        """Format ``record`` and replace every secret in the result with ``[redacted]``."""
        text = self.inner.format(record) if self.inner is not None else super().format(record)
        return redact_text(text, self._registry.all())


def _wrap(handler: logging.Handler) -> None:
    if isinstance(handler.formatter, RedactingFormatter):
        return
    handler.setFormatter(RedactingFormatter(inner=handler.formatter or logging.Formatter()))


def install_redacting_formatter() -> None:
    """Wrap the formatter of every root and uvicorn handler in a RedactingFormatter.

    Idempotent. If the root logger has no handler, adds a stderr handler at INFO, so app logs
    never fall through to logging's unredacted last-resort handler.
    """
    root = logging.getLogger()
    if not root.handlers:
        stderr = logging.StreamHandler(sys.stderr)
        stderr.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(stderr)
        if root.level == logging.WARNING:
            root.setLevel(logging.INFO)
    for handler in root.handlers:
        _wrap(handler)
    for name in _UVICORN_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            _wrap(handler)

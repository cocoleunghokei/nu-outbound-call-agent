"""Replay a recorded Vapi conversation (one JSON body per line) into a local backend.

Usage: ``uv run python -m app.dev.replay FIXTURE CALL_ID [--url U] [--type T] [--limit N]``.
The secret comes from ``VAPI_WEBHOOK_SECRET`` or ``backend/.env``. Every body's
``message.call.metadata.call_id`` is rewritten to CALL_ID so it binds to the seeded call.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

DEFAULT_URL = "http://localhost:8000/webhooks/vapi"
SECRET_HEADER = "X-Vapi-Secret"


def _rewrite_call_id(body: dict[str, Any], call_id: uuid.UUID) -> None:
    message = body.get("message")
    call = message.get("call") if isinstance(message, dict) else None
    if not isinstance(call, dict):
        raise ValueError("fixture line has no message.call object")
    metadata = call.setdefault("metadata", {})
    metadata["call_id"] = str(call_id)


def replay_fixture(
    fixture: Path,
    call_id: uuid.UUID,
    url: str,
    secret: str,
    client: httpx.Client,
    message_type: str | None = None,
    limit: int | None = None,
) -> list[int]:
    """POST each fixture line (optionally only one message type, at most ``limit``).

    Returns the status codes. Raises RuntimeError on the first non-2xx answer.
    """
    statuses: list[int] = []
    for line in fixture.read_text().splitlines():
        if not line.strip():
            continue
        body = json.loads(line)
        if message_type is not None and body.get("message", {}).get("type") != message_type:
            continue
        if limit is not None and len(statuses) >= limit:
            break
        _rewrite_call_id(body, call_id)
        response = client.post(url, json=body, headers={SECRET_HEADER: secret}, timeout=15)
        statuses.append(response.status_code)
        if not response.is_success:
            raise RuntimeError(f"webhook answered {response.status_code}: {response.text[:200]}")
    return statuses


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("call_id", type=uuid.UUID)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--type", dest="message_type", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    secret = Settings().vapi_webhook_secret.get_secret_value()
    if not secret:
        print(
            "set VAPI_WEBHOOK_SECRET (env or backend/.env) to the server's secret", file=sys.stderr
        )
        return 2
    with httpx.Client() as client:
        try:
            statuses = replay_fixture(
                args.fixture, args.call_id, args.url, secret, client, args.message_type, args.limit
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            print(f"replay failed: {exc}", file=sys.stderr)
            return 1
    print(f"posted {len(statuses)} message(s): {statuses}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

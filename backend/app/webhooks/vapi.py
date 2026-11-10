"""POST /webhooks/vapi: the one entry point for every Vapi server message.

Order of work (docs/architecture.md sections 3, 4.0 to 4.3):

1. Authenticate (``X-Vapi-Secret``, optional IP allowlist). A failure is a 401; nothing is
   written and only the reason is logged.
2. Partial transcripts are acked at once and never stored.
3. In one transaction: bind the body to a call, take the call lock, capture the capability
   URLs into ``call_secrets`` (only while the call has not ended), insert the redacted body
   into ``vapi_raw_events``. An unbound body stops here (raw-only).
4. Each unit (one per ``toolCallList`` entry, else the whole message) runs in its own
   savepoint; a failing unit is rolled back and dead-lettered into ``call_events_failed``
   without touching its siblings.
5. Any database outage (pool, statements, COMMIT) is a 503 ``db_unavailable`` with
   ``retryable=true``. The 200 is sent only after COMMIT succeeds.

Lock order: the call row (``lock_call``) is locked before ``call_secrets``, ``tool_calls``
and ``call_events``, here and in ``dispatch_pending``.
"""

import hmac
import ipaddress
import json
import logging
import traceback
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from app.calls_lock import CallNotFound, lock_call, write_call_secrets
from app.config import Settings
from app.errors import ApiError
from app.events import append_event
from app.registry import Registries
from app.secrets_registry import get_secret_registry
from app.webhooks.bind import BoundCall, bind_call, message_of, vapi_call_id_of, vapi_call_of
from app.webhooks.normalise import (
    SCORE_FIELDS,
    RawUnit,
    ToolCallUnit,
    TranscriptUnit,
    is_partial_transcript,
    message_type,
    normalise_unit,
    split_units,
)
from app.webhooks.redact import redact, redact_text
from app.webhooks.registry import ToolCallContext, ToolHandler, ToolRegistry

__all__ = ["collect_secrets", "dispatch_pending", "router", "run_ingress_startup"]

logger = logging.getLogger(__name__)

router = APIRouter()

SECRET_HEADER = "X-Vapi-Secret"
VAPI_NETWORK = ipaddress.ip_network("167.150.224.0/23")
RECEIVED = "received"
# Tools whose arguments carry confidence/dimensions; those go to agent_scores (DC-5).
SCORED_TOOLS = frozenset({"ask_user", "report_decision"})
_CAPABILITY_KEYS = frozenset({"controlUrl", "listenUrl"})
_MAX_ERROR_CHARS = 8000
# A receipt keeps confidence/dimensions only while pending (dispatch_pending needs them to
# write agent_scores); leaving pending strips them in the same UPDATE (DC-5).
_STRIP_SCORES = "arguments - 'confidence' - 'dimensions'"


class _DatabaseUnavailable(Exception):
    """A database failure that Vapi should retry (raw insert or COMMIT failed)."""


def _db_unavailable() -> ApiError:
    return ApiError("db_unavailable", 503, "Database unavailable, retry later", True)


def _unauthorized(reason: str) -> ApiError:
    logger.warning("vapi webhook rejected: %s", reason)
    return ApiError("unauthorized", 401, "Webhook authentication failed", False)


def _authenticate(request: Request, settings: Settings) -> None:
    """Raise a 401 ApiError unless the secret header (and, if enabled, the source IP) match."""
    expected = settings.vapi_webhook_secret.get_secret_value()
    if not expected:
        raise _unauthorized("no webhook secret is configured")
    got = request.headers.get(SECRET_HEADER, "")
    if not hmac.compare_digest(got.encode(), expected.encode()):
        raise _unauthorized("bad or missing secret")
    if settings.vapi_ip_allowlist:
        host = request.client.host if request.client else ""
        try:
            allowed = ipaddress.ip_address(host) in VAPI_NETWORK
        except ValueError:
            allowed = False
        if not allowed:
            raise _unauthorized("source address outside the Vapi range")


def _find_capability_urls(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _CAPABILITY_KEYS and isinstance(value, str) and value:
                found.add(value)
            else:
                _find_capability_urls(value, found)
    elif isinstance(node, list):
        for item in node:
            _find_capability_urls(item, found)


def collect_secrets(body: Any, settings: Settings) -> set[str]:
    """The literal secrets in this request: every ``controlUrl``/``listenUrl`` value anywhere
    in the body (``monitor.controlUrl``, ``monitor.listenUrl``) plus the webhook secret."""
    found: set[str] = set()
    _find_capability_urls(body, found)
    webhook_secret = settings.vapi_webhook_secret.get_secret_value()
    if webhook_secret:
        found.add(webhook_secret)
    return found


def _monitor_urls(body: Any) -> tuple[str | None, str | None]:
    monitor = vapi_call_of(body).get("monitor")
    if not isinstance(monitor, dict):
        return None, None
    control, listen = monitor.get("controlUrl"), monitor.get("listenUrl")
    return (
        control if isinstance(control, str) and control else None,
        listen if isinstance(listen, str) and listen else None,
    )


def format_error(exc: BaseException, secrets: set[str] | frozenset[str]) -> str:
    """The formatted exception (with traceback), redacted and truncated for storage."""
    text = "".join(traceback.format_exception(exc))
    return redact_text(text, secrets)[-_MAX_ERROR_CHARS:]


async def _dead_letter(
    conn: AsyncConnection[Any], call_id: UUID | None, raw_event_id: str, error: str
) -> None:
    await conn.execute(
        "insert into public.call_events_failed (call_id, raw_event_id, error) values (%s, %s, %s)",
        (call_id, raw_event_id, error),
    )


def _without_scores(arguments: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in arguments.items() if k not in SCORE_FIELDS}


async def _store_scores(
    conn: AsyncConnection[Any], ctx: ToolCallContext, arguments: dict[str, Any]
) -> None:
    if ctx.name not in SCORED_TOOLS:
        return
    confidence, dimensions = arguments.get("confidence"), arguments.get("dimensions")
    if confidence is None or dimensions is None:
        return
    await conn.execute(
        "insert into public.agent_scores (call_id, tool_call_id, tool, confidence, dimensions)"
        " values (%s, %s, %s, %s, %s) on conflict (call_id, tool_call_id) do nothing",
        (ctx.call_id, ctx.tool_call_id, ctx.name, confidence, Jsonb(dimensions)),
    )


async def run_tool_handler(
    conn: AsyncConnection[Any],
    ctx: ToolCallContext,
    handler: ToolHandler,
    *,
    arguments: dict[str, Any],
    raw_event_id: str,
    secrets: set[str] | frozenset[str],
) -> tuple[bool, str]:
    """Run ``handler`` for a receipt that the caller holds locked; return (ok, result).

    The handler runs in a nested savepoint. On success the receipt becomes ``handled`` with
    the result and the scores (from the full ``arguments``) go to ``agent_scores``. On failure
    only that savepoint is rolled back; the receipt becomes ``failed`` with the redacted error
    and a ``call_events_failed`` row is written, so a redelivery never reruns the handler.
    A ``psycopg.OperationalError`` (lock timeout, query cancel, deadlock, serialization
    failure, lost connection) is transient and is re-raised instead, so the caller's
    transaction rolls back with the receipt and the work is retried later.
    Either way ``confidence``/``dimensions`` are removed from the receipt's ``arguments`` in
    the same UPDATE, so after the pending state scores live only in ``agent_scores``.
    """
    try:
        async with conn.transaction():
            result = await handler(conn, ctx)
            if not isinstance(result, str):
                raise TypeError(f"tool handler for {ctx.name} returned {type(result).__name__}")
            result = redact_text(result, secrets)
            await conn.execute(
                "update public.tool_calls set state = 'handled', result = %s, error = null,"
                f" arguments = {_STRIP_SCORES} where call_id = %s and tool_call_id = %s",
                (Jsonb(result), ctx.call_id, ctx.tool_call_id),
            )
            await _store_scores(conn, ctx, arguments)
    except psycopg.OperationalError:
        # Transient (lock timeout, cancel, deadlock, serialization, connection): not the
        # handler's fault. Re-raise so the caller's whole transaction rolls back and the
        # receipt is retried: a 503 to Vapi on ingress, still pending in dispatch_pending.
        raise
    except Exception as exc:
        error = format_error(exc, secrets)
        await conn.execute(
            "update public.tool_calls set state = 'failed', error = %s,"
            f" arguments = {_STRIP_SCORES} where call_id = %s and tool_call_id = %s",
            (error, ctx.call_id, ctx.tool_call_id),
        )
        await _dead_letter(conn, ctx.call_id, raw_event_id, error)
        logger.warning(
            "tool handler %s failed for call %s (%s)", ctx.name, ctx.call_id, type(exc).__name__
        )
        return False, RECEIVED
    return True, result


async def _receive_tool_call(
    conn: AsyncConnection[Any],
    call: BoundCall,
    unit: ToolCallUnit,
    raw_event_id: str,
    secrets: set[str],
    tools: ToolRegistry,
) -> str:
    """Write the receipt, then run the handler once. Returns the result for Vapi."""
    await lock_call(conn, call.call_id)
    inserted = await (
        await conn.execute(
            "insert into public.tool_calls (call_id, tool_call_id, name, arguments, source_ts)"
            " values (%s, %s, %s, %s, %s) on conflict (call_id, tool_call_id) do nothing"
            " returning tool_call_id",
            (call.call_id, unit.tool_call_id, unit.name, Jsonb(unit.arguments), unit.source_ts),
        )
    ).fetchone()
    if inserted is None:
        stored = await (
            await conn.execute(
                "select state, result from public.tool_calls"
                " where call_id = %s and tool_call_id = %s",
                (call.call_id, unit.tool_call_id),
            )
        ).fetchone()
        if stored is not None and stored[0] == "handled" and isinstance(stored[1], str):
            return stored[1]
        return RECEIVED
    handler = tools.get(unit.name)
    if handler is None:
        return RECEIVED
    ctx = ToolCallContext(
        call_id=call.call_id,
        user_id=call.user_id,
        tool_call_id=unit.tool_call_id,
        name=unit.name,
        arguments=_without_scores(unit.arguments),
        source_ts=unit.source_ts,
    )
    _, result = await run_tool_handler(
        conn,
        ctx,
        handler,
        arguments=unit.arguments,
        raw_event_id=raw_event_id,
        secrets=secrets,
    )
    return result


async def _process_unit(
    conn: AsyncConnection[Any],
    call: BoundCall,
    unit: RawUnit,
    redacted_body: dict[str, Any],
    raw_event_id: str,
    secrets: set[str],
    reg: Registries,
) -> dict[str, str] | None:
    """Run one unit under its own savepoint; dead-letter it on failure.

    Returns the ``{"toolCallId", "result"}`` entry for tool calls, else None.
    """
    tool_call_id = unit.tool_call_id
    try:
        async with conn.transaction():
            normalised = normalise_unit(unit, secrets)
            if isinstance(normalised, TranscriptUnit):
                await append_event(
                    conn,
                    call_id=call.call_id,
                    type="transcript.final",
                    source="vapi",
                    payload=normalised.payload,
                    occurred_at=normalised.occurred_at,
                    dedup_key=normalised.dedup_key,
                )
                return None
            if isinstance(normalised, ToolCallUnit):
                result = await _receive_tool_call(
                    conn, call, normalised, raw_event_id, secrets, reg.tools
                )
                return {"toolCallId": normalised.tool_call_id, "result": result}
            handler = reg.events.get(normalised.vapi_type)
            if handler is not None:
                await handler(conn, call, redacted_body)
            return None
    except psycopg.OperationalError:
        raise
    except Exception as exc:
        await _dead_letter(conn, call.call_id, raw_event_id, format_error(exc, secrets))
        logger.warning(
            "vapi webhook unit %d of raw event %s failed for call %s (%s)",
            unit.index,
            raw_event_id,
            call.call_id,
            type(exc).__name__,
        )
        return {"toolCallId": tool_call_id, "result": RECEIVED} if tool_call_id else None


async def _ingest(
    conn: AsyncConnection[Any],
    body: dict[str, Any],
    redacted_body: dict[str, Any],
    secrets: set[str],
    reg: Registries,
) -> dict[str, Any]:
    """Steps 3 and 4 inside the caller's transaction; returns the response body."""
    call = await bind_call(conn, body)
    if call is not None:
        try:
            locked = await lock_call(conn, call.call_id)
        except CallNotFound:
            call = None
        else:
            if locked.vapi_call_id is not None and locked.vapi_call_id != vapi_call_id_of(body):
                call = None  # the binding changed while we waited for the lock
            else:
                # Rebuild from the locked row: bind_call's read was unlocked, so its ended_at
                # can be stale, and handlers decide on it.
                call = BoundCall(
                    call_id=locked.id,
                    user_id=locked.user_id,
                    vapi_call_id=locked.vapi_call_id,
                    ended_at=locked.ended_at,
                )
                control, listen = _monitor_urls(body)
                if locked.ended_at is None and control and listen:
                    await write_call_secrets(conn, call.call_id, control, listen)
    message = message_of(redacted_body)
    try:
        inserted = await (
            await conn.execute(
                "insert into public.vapi_raw_events (call_id, vapi_call_id, message_type, body)"
                " values (%s, %s, %s, %s) returning id",
                (
                    call.call_id if call else None,
                    vapi_call_id_of(body),
                    message_type(message),
                    Jsonb(redacted_body),
                ),
            )
        ).fetchone()
    except psycopg.Error as exc:
        raise _DatabaseUnavailable("raw event insert failed") from exc
    if call is None or inserted is None:
        return {}
    raw_event_id = str(inserted[0])
    results: list[dict[str, str]] = []
    for unit in split_units(redacted_body):
        entry = await _process_unit(conn, call, unit, redacted_body, raw_event_id, secrets, reg)
        if entry is not None:
            results.append(entry)
    if message_type(message) == "tool-calls":
        return {"results": results}
    return {}


@router.post("/webhooks/vapi")
async def vapi_webhook(request: Request) -> dict[str, Any]:
    """Receive one Vapi server message. See the module docstring for the order of work."""
    settings: Settings = request.app.state.settings
    _authenticate(request, settings)
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise ApiError("invalid_request", 400, "Body is not JSON", False) from None
    if not isinstance(body, dict) or not message_of(body):
        raise ApiError("invalid_request", 400, "Body has no message object", False)
    if is_partial_transcript(message_of(body)):
        return {}

    registry = get_secret_registry()
    request_secrets = collect_secrets(body, settings)
    for value in request_secrets:
        registry.add(value)  # seen since the process started: redacted from every log line
    secrets = set(registry.all()) | request_secrets
    redacted_body: dict[str, Any] = redact(body, secrets)

    pool: AsyncConnectionPool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise _db_unavailable()
    reg: Registries = request.app.state.registries
    try:
        async with pool.connection() as conn:
            response = await _ingest(conn, body, redacted_body, secrets, reg)
            try:
                await conn.commit()
            except psycopg.Error as exc:
                raise _DatabaseUnavailable("commit failed") from exc
    except (psycopg.OperationalError, PoolTimeout, _DatabaseUnavailable) as exc:
        logger.warning("vapi webhook not stored, answering 503 (%s)", type(exc).__name__)
        raise _db_unavailable() from None
    return response


async def dispatch_pending(
    pool: AsyncConnectionPool, limit: int = 50, *, tools: ToolRegistry
) -> int:
    """Run registered handlers on ``pending`` tool calls, oldest first, each exactly once.

    Reads candidates without locks, then gives each its own transaction: ``lock_call``, then
    ``select ... for update skip locked`` on the still-pending receipt (a row that is gone,
    handled or locked by another dispatcher is skipped). A failing handler marks its receipt
    ``failed`` and writes a dead letter. A transient database error (``OperationalError``)
    rolls back only that candidate's transaction, is logged, and leaves the row ``pending``
    for the next run. Never raises. Returns the number handled.

    ``tools`` is the app's tool registry. The internal startup hook passes it; Coco's sweeper
    is a worker that gets the pool and registry from its ``AppContext``::

        async def sweeper(ctx: AppContext) -> None:
            while True:
                if ctx.pool is not None:
                    await dispatch_pending(ctx.pool, tools=ctx.registries.tools)
                await asyncio.sleep(5)

        def register(reg: Registries) -> None:
            reg.add_worker(sweeper)
    """
    names = tools.names()
    if not names:
        return 0
    secrets = get_secret_registry().all()
    try:
        async with pool.connection() as conn:
            candidates = await (
                await conn.execute(
                    "select call_id, tool_call_id from public.tool_calls"
                    " where state = 'pending' and name = any(%s)"
                    " order by created_at, call_id, tool_call_id limit %s",
                    (names, limit),
                )
            ).fetchall()
    except Exception:
        logger.exception("dispatch_pending could not read pending tool calls")
        return 0
    handled = failed = 0
    for call_id, tool_call_id in candidates:
        try:
            async with pool.connection() as conn:
                async with conn.transaction():
                    call = await lock_call(conn, call_id)
                    row = await (
                        await conn.execute(
                            "select name, arguments, source_ts from public.tool_calls"
                            " where call_id = %s and tool_call_id = %s and state = 'pending'"
                            " for update skip locked",
                            (call_id, tool_call_id),
                        )
                    ).fetchone()
                    if row is None:
                        continue
                    name, arguments, source_ts = row
                    handler = tools.get(name)
                    if handler is None or not isinstance(arguments, dict):
                        continue
                    ctx = ToolCallContext(
                        call_id=call.id,
                        user_id=call.user_id,
                        tool_call_id=tool_call_id,
                        name=name,
                        arguments=_without_scores(arguments),
                        source_ts=source_ts,
                    )
                    ok, _ = await run_tool_handler(
                        conn,
                        ctx,
                        handler,
                        arguments=arguments,
                        raw_event_id=f"tool_calls:{tool_call_id}",
                        secrets=secrets,
                    )
            if ok:
                handled += 1
            else:
                failed += 1
        except CallNotFound:
            continue
        except psycopg.OperationalError as exc:
            logger.warning(
                "dispatch_pending: transient database error on a tool call of call %s,"
                " left pending (%s)",
                call_id,
                type(exc).__name__,
            )
        except Exception:
            logger.exception("dispatch_pending failed on a tool call of call %s", call_id)
    if handled or failed:
        logger.info("dispatch_pending: %d handled, %d failed", handled, failed)
    return handled


async def run_ingress_startup(pool: AsyncConnectionPool, tools: ToolRegistry) -> None:
    """Startup: load ``call_secrets`` into the secret registry, then run dispatch_pending.

    Never raises, so a database hiccup cannot stop the app from starting.
    """
    try:
        async with pool.connection() as conn:
            loaded = await get_secret_registry().load_call_secrets(conn)
        logger.info("loaded capability URLs of %d live calls into the secret registry", loaded)
    except Exception:
        logger.exception("could not load call_secrets at startup")
    handled = await dispatch_pending(pool, tools=tools)
    logger.info("startup dispatch_pending handled %d tool calls", handled)

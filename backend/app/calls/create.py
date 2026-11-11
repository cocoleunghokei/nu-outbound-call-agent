"""``POST /calls``: start a reviewed brief as a real phone call (PC-6 -> DC-3).

Follows the call-creation rule in docs/architecture.md section 4.1: at most one Vapi call per
Start, and an explicit uncertain state instead of a blind retry.

1. One transaction: claim the ``Idempotency-Key``, lock the brief, check it again on the
   server (contract shape, PC-7 number rules, readiness against the profile *now*, no other
   call from this brief still running), build the assistant config, then insert the call as
   ``creating`` with its profile snapshot and ``create_attempted_at``. Commit.
2. No transaction: ``voice.create_call`` with our ``call_id`` in the Vapi metadata, so
   webhooks bind to the call even before ``vapi_call_id`` is stored.
3. One transaction under the call lock, recording what happened:
   - placed: store ``vapi_call_id`` and the control URL, move ``creating -> dialing``;
   - provably not placed (a 4xx, no connection, an unsafe config): ``failed``;
   - unknown (timeout, 5xx, unreadable answer): ``creation_uncertain``. Nothing is resent.
     Webhooks still bind by ``call_id`` and move the call on if Vapi did place it.

Every answer after step 1 is ``201 {"call_id", "status"}``, so the screen can open the call
and follow its events. A replay of the same key returns the same call, never a second one.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from app.auth import current_user
from app.briefs.phone import check_phone
from app.briefs.readiness import check_readiness
from app.briefs.store import get_brief, load_snapshot
from app.calls.assistant import build_assistant_config
from app.calls.store import set_status
from app.calls_lock import TERMINAL_STATUSES, lock_call, write_call_secrets
from app.config import Settings
from app.contracts.generated import Brief, CreateCallCommand
from app.db import get_pool
from app.errors import ApiError
from app.events import append_event
from app.idempotency import claim_idempotency, idempotency_key, store_idempotent_response
from app.voice.port import (
    CreatedCall,
    UnsafeAssistantConfig,
    VoiceNotSent,
    VoicePlatform,
    VoiceRejected,
    VoiceUncertain,
    prepare_assistant,
)
from app.voice.prompt_safety import AskFirstInPrompt, MissingVar, Snapshot
from app.voice.vapi import get_voice

router = APIRouter()
logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/vapi"
# Used only with the fake voice platform, which never calls back.
FAKE_SERVER_URL = "http://localhost:8000"
# calls.max_duration_s (DC-15). Kept equal to the column default in the migration.
MAX_DURATION_S = 900
# Statuses a call can be placed (or failed) from: the reconciler may have moved a slow
# ``creating`` call to ``creation_uncertain`` before its result was recorded.
NOT_YET_PLACED = frozenset({"creating", "creation_uncertain"})


def get_call_voice(request: Request) -> VoicePlatform:
    """FastAPI dependency: the app's voice platform, created once per app.

    Tests replace it with ``app.dependency_overrides[get_call_voice]``.
    """
    voice: VoicePlatform | None = getattr(request.app.state, "call_voice", None)
    if voice is None:
        voice = get_voice(request.app.state.settings)
        request.app.state.call_voice = voice
    return voice


@dataclass(frozen=True)
class PreparedCall:
    """What step 1 committed and step 2 needs. ``assistant`` holds a secret: never log it."""

    call_id: uuid.UUID
    phone_e164: str
    assistant: dict[str, Any]
    snapshot: Snapshot


def _server_url(settings: Settings) -> tuple[str, str]:
    """The webhook URL and secret Vapi calls back with. 503 when a real call can't be wired.

    A real call needs the Vapi API key, the phone number id, the webhook secret and an https
    ``PUBLIC_BASE_URL``: the webhook secret travels in that URL's requests, so never over http.
    """
    secret = settings.vapi_webhook_secret.get_secret_value()
    if settings.voice_platform == "fake":
        return f"{FAKE_SERVER_URL}{WEBHOOK_PATH}", secret
    base = settings.public_base_url.strip().rstrip("/")
    configured = (
        settings.vapi_api_key.get_secret_value()
        and settings.vapi_phone_number_id
        and secret
        and _is_https_origin(base)
    )
    if not configured:
        raise ApiError("calls_not_configured", 503, "Calling isn't set up on the server yet", False)
    return f"{base}{WEBHOOK_PATH}", secret


def _is_https_origin(url: str) -> bool:
    """True for an absolute ``https://host`` URL (a path is allowed, a query or fragment not)."""
    parts = urlsplit(url)
    return (
        parts.scheme == "https" and bool(parts.hostname) and not parts.query and not parts.fragment
    )


def _not_ready(missing: list[str]) -> ApiError:
    return ApiError(
        "brief_not_ready", 422, f"Still needed before calling: {', '.join(missing)}", False
    )


async def _has_running_call(
    conn: AsyncConnection[Any], user_id: uuid.UUID, brief_id: uuid.UUID
) -> bool:
    row = await (
        await conn.execute(
            "select exists (select 1 from public.calls where brief_id = %s and user_id = %s"
            " and ended_at is null and status <> all(%s))",
            (brief_id, user_id, list(TERMINAL_STATUSES)),
        )
    ).fetchone()
    return bool(row and row[0])


async def _copy_snapshot(
    conn: AsyncConnection[Any], call_id: uuid.UUID, snapshot: Snapshot
) -> None:
    """Freeze the profile for this call: the agent and the ask manager read only this copy."""
    for key, (value, sharing) in snapshot.items():
        await conn.execute(
            "insert into public.call_profile_snapshots (call_id, key, value, sharing)"
            " values (%s, %s, %s, %s)",
            (call_id, key, value, sharing),
        )


async def prepare_call(
    conn: AsyncConnection[Any],
    user_id: uuid.UUID,
    brief_id: uuid.UUID,
    settings: Settings,
) -> PreparedCall:
    """Step 1, in the caller's transaction: check the brief and insert the ``creating`` call.

    Raises ApiError 404 ``brief_not_found``, 422 ``invalid_brief``, ``invalid_phone`` or
    ``brief_not_ready``, 409 ``call_in_progress`` or 503 ``calls_not_configured``; then
    nothing is written.
    """
    server_url, webhook_secret = _server_url(settings)
    # Locked so a PATCH /briefs can't change the brief between this check and the dial.
    stored = await get_brief(conn, user_id, brief_id, for_update=True)
    if stored is None:
        raise ApiError("brief_not_found", 404, "Brief not found", False)
    try:
        brief = Brief.model_validate(stored.brief)
    except ValidationError:
        raise ApiError(
            "invalid_brief", 422, "This plan is incomplete. Edit it and try again", False
        ) from None
    phone = check_phone(brief.phone_e164)
    if phone.e164 is None:
        raise ApiError("invalid_phone", 422, phone.message, False)
    snapshot = await load_snapshot(conn, user_id)
    readiness = check_readiness(stored.brief, snapshot)
    if not readiness.ready:
        raise _not_ready(readiness.missing_fields)
    if await _has_running_call(conn, user_id, brief_id):
        raise ApiError("call_in_progress", 409, "A call from this plan is already running", False)
    try:
        assistant = build_assistant_config(
            brief,
            snapshot,
            server_url=server_url,
            webhook_secret=webhook_secret,
            max_duration_s=MAX_DURATION_S,
        )
        # The same check create_call runs before sending, run now so a refusal is a 422
        # before any call row exists, not a failed call on every Start.
        prepare_assistant(assistant, snapshot)
    except (MissingVar, AskFirstInPrompt) as exc:
        raise _not_ready([exc.key]) from None
    except UnsafeAssistantConfig as exc:
        raise ApiError("unsafe_assistant_config", 422, exc.message, False) from None

    row = await (
        await conn.execute(
            "insert into public.calls (user_id, brief_id, status, max_duration_s)"
            " values (%s, %s, 'creating', %s) returning id",
            (user_id, brief_id, MAX_DURATION_S),
        )
    ).fetchone()
    assert row is not None
    call_id: uuid.UUID = row[0]
    await _copy_snapshot(conn, call_id, snapshot)
    await append_event(
        conn,
        call_id=call_id,
        type="status.changed",
        source="backend",
        payload={"status": "creating"},
        occurred_at=datetime.now(UTC),
    )
    # Committed before the request is sent: a crash mid-request leaves a visible attempt.
    await conn.execute(
        "update public.calls set create_attempted_at = now() where id = %s", (call_id,)
    )
    return PreparedCall(
        call_id=call_id, phone_e164=phone.e164, assistant=assistant, snapshot=snapshot
    )


async def record_placed(
    conn: AsyncConnection[Any], call_id: uuid.UUID, created: CreatedCall
) -> str:
    """Step 3 on success: store Vapi's id and the control URL, ``creating -> dialing``."""
    call = await lock_call(conn, call_id)
    # Stored even if a webhook already ended the call (a fast no-answer): the row must keep
    # its link to the billed Vapi call.
    await conn.execute(
        "update public.calls set vapi_call_id = %s where id = %s and vapi_call_id is null",
        (created.vapi_call_id, call_id),
    )
    if call.ended_at is not None or call.status in TERMINAL_STATUSES:
        return call.status  # a webhook already ended it
    if created.control_url:
        await write_call_secrets(conn, call_id, created.control_url, created.listen_url or "")
    await set_status(conn, call_id, "dialing", only_from=NOT_YET_PLACED)
    return (await lock_call(conn, call_id)).status


async def record_not_placed(
    conn: AsyncConnection[Any], call_id: uuid.UUID, *, uncertain: bool
) -> str:
    """Step 3 on failure: ``creation_uncertain`` if Vapi may have placed it, else ``failed``.

    Also used by the reconciler (``app/calls/reconcile.py``) to fail an uncertain call that
    was never found.
    """
    call = await lock_call(conn, call_id)
    if call.status not in NOT_YET_PLACED or call.vapi_call_id is not None:
        return call.status  # a webhook got here first: Vapi did place it
    if uncertain:
        await set_status(conn, call_id, "creation_uncertain", only_from=frozenset({"creating"}))
        return "creation_uncertain"
    await conn.execute(
        "update public.calls set status = 'failed', outcome = 'failed', ended_at = now()"
        " where id = %s",
        (call_id,),
    )
    await append_event(
        conn,
        call_id=call_id,
        type="status.changed",
        source="backend",
        payload={"status": "failed"},
        occurred_at=datetime.now(UTC),
    )
    return "failed"


async def place_call(
    pool: AsyncConnectionPool,
    voice: VoicePlatform,
    prepared: PreparedCall,
    *,
    user_id: uuid.UUID,
    key: uuid.UUID,
) -> str:
    """Steps 2 and 3: send the one create request, then record the result. Returns the status.

    The final ``{call_id, status}`` is saved for the ``Idempotency-Key`` in step 3's own
    transaction, so a replay never says ``creating`` after the call has moved on. If step 3
    can't be written, the call stays ``creating`` and the reconciler (``reconcile.py``) picks
    it up: it becomes ``creation_uncertain`` and is looked up at Vapi by our ``call_id``.
    """
    call_id = prepared.call_id
    created: CreatedCall | None = None
    uncertain = False
    try:
        created = await voice.create_call(
            prepared.assistant,
            prepared.phone_e164,
            {"call_id": str(call_id)},
            prepared.snapshot,
        )
    except VoiceUncertain as exc:
        uncertain = True
        logger.warning("call %s: creation outcome unknown (%s)", call_id, exc)
    except VoiceRejected as exc:
        logger.warning("call %s: voice platform refused the call (%s)", call_id, exc)
    except VoiceNotSent:
        logger.warning("call %s: voice platform unreachable, call not placed", call_id)
    except (UnsafeAssistantConfig, AskFirstInPrompt) as exc:
        # Raised before anything is sent. Never log the config itself.
        logger.error(
            "call %s: assistant config refused before sending (%s)", call_id, type(exc).__name__
        )
    except Exception:
        # Anything else may have happened after the request left: treat it as unknown.
        uncertain = True
        logger.exception("call %s: unexpected error while placing the call", call_id)

    try:
        async with pool.connection() as conn, conn.transaction():
            if created is not None:
                status = await record_placed(conn, call_id, created)
            else:
                status = await record_not_placed(conn, call_id, uncertain=uncertain)
            await store_idempotent_response(
                conn, user_id, key, 201, {"call_id": str(call_id), "status": status}
            )
            return status
    except Exception:
        # Vapi's id is not a secret, and it is the only trace of a placed call until the
        # reconciler finds it again.
        logger.exception(
            "call %s: could not record the result (vapi call %s)",
            call_id,
            created.vapi_call_id if created else None,
        )
        raise


@router.post("/calls", status_code=201)
async def create_call(
    body: CreateCallCommand,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user)],
    key: Annotated[uuid.UUID, Depends(idempotency_key)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    voice: Annotated[VoicePlatform, Depends(get_call_voice)],
) -> JSONResponse:
    """Start the brief as a call. Returns ``201 {"call_id", "status"}``.

    ``status`` is ``dialing`` when Vapi accepted the call, ``failed`` when it provably wasn't
    placed, and ``creation_uncertain`` when we can't tell yet. Errors before a call exists:
    404 ``brief_not_found``, 422 ``invalid_brief`` / ``invalid_phone`` / ``brief_not_ready`` /
    ``unsafe_assistant_config``, 409 ``call_in_progress``, 503 ``calls_not_configured``.
    """
    settings: Settings = request.app.state.settings
    async with pool.connection() as conn, conn.transaction():
        stored = await claim_idempotency(conn, user_id, key)
        if stored is not None:
            return JSONResponse(stored.body, status_code=stored.status_code)
        prepared = await prepare_call(conn, user_id, body.brief_id, settings)
        # Stored now, so a replay while the request is in flight returns this call, not a new one.
        first = {"call_id": str(prepared.call_id), "status": "creating"}
        await store_idempotent_response(conn, user_id, key, 201, first)

    status = await place_call(pool, voice, prepared, user_id=user_id, key=key)
    return JSONResponse({"call_id": str(prepared.call_id), "status": status}, status_code=201)

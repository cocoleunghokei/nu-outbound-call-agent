"""Brief API (PC-3 to PC-7): save the brief the pre-call chat built, read it, replace it.

- ``POST /briefs``: create. Body is a ``brief.v1`` brief. Needs ``Idempotency-Key``. 201.
- ``GET /briefs/{brief_id}``: read, with readiness checked against the profile now.
- ``PATCH /briefs/{brief_id}``: replace after the user edits the review card (PC-6). The
  body is the whole brief (the chat always sends the full reviewed brief). PATCH, not PUT,
  because the shared CORS setup in ``main.py`` allows PATCH from the browser. Needs
  ``Idempotency-Key``. 409 ``brief_in_use`` once a call was started from it.

Every response: ``{brief_id, brief, missing_fields, opening_line, created_at}``. Start is
allowed only when ``missing_fields`` is empty; ``POST /calls`` (Coco) checks again.
No LLM here yet (plan Step 10): the browser sends the brief it built.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.auth import current_user
from app.briefs.phone import check_phone
from app.briefs.readiness import Readiness, check_readiness
from app.briefs.store import (
    StoredBrief,
    brief_has_call,
    get_brief,
    insert_brief,
    load_snapshot,
    update_brief,
)
from app.contracts import validator_for
from app.db import get_pool
from app.errors import ApiError
from app.idempotency import claim_idempotency, idempotency_key, store_idempotent_response

router = APIRouter(prefix="/briefs", tags=["briefs"])

UserId = Annotated[uuid.UUID, Depends(current_user)]
IdempotencyKey = Annotated[uuid.UUID, Depends(idempotency_key)]
BriefBody = Annotated[dict[str, Any], Body()]


def _not_found() -> ApiError:
    # Same answer for "doesn't exist" and "not yours", so ids can't be probed.
    return ApiError("brief_not_found", 404, "Brief not found", False)


def validate_brief(body: dict[str, Any]) -> dict[str, Any]:
    """Check the body against contracts/brief.v1.json and the PC-7 number rules.

    Raises 422 ``invalid_brief`` (first schema error, with its path) or 422 ``invalid_phone``
    (with the same plain message the chat shows). Returns the brief with its number normalised.
    """
    errors = sorted(validator_for("brief.v1.json").iter_errors(body), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = ".".join(str(part) for part in first.absolute_path) or "brief"
        raise ApiError("invalid_brief", 422, f"{where}: {first.message}", False)
    phone = check_phone(str(body["phone_e164"]))
    if not phone.ok:
        raise ApiError("invalid_phone", 422, phone.message, False)
    return {**body, "phone_e164": phone.e164}


def _response(stored: StoredBrief, readiness: Readiness) -> dict[str, Any]:
    return {
        "brief_id": str(stored.id),
        "brief": stored.brief,
        "missing_fields": readiness.missing_fields,
        "opening_line": readiness.opening_line,
        "created_at": stored.created_at.isoformat(),
    }


@router.post("", status_code=201, response_model=None)
async def create_brief(
    request: Request, user_id: UserId, key: IdempotencyKey, body: BriefBody
) -> dict[str, Any] | JSONResponse:
    """Save a new brief for the caller and report what's still missing."""
    brief = validate_brief(body)
    pool = get_pool(request)
    async with pool.connection() as conn, conn.transaction():
        replay = await claim_idempotency(conn, user_id, key)
        if replay is not None:
            return JSONResponse(status_code=replay.status_code, content=replay.body)
        readiness = check_readiness(brief, await load_snapshot(conn, user_id))
        stored = await insert_brief(conn, user_id, brief, readiness.missing_fields)
        result = _response(stored, readiness)
        await store_idempotent_response(conn, user_id, key, 201, result)
    return result


@router.get("/{brief_id}")
async def read_brief(request: Request, user_id: UserId, brief_id: uuid.UUID) -> dict[str, Any]:
    """The caller's brief, with readiness checked against their profile as it is now."""
    pool = get_pool(request)
    async with pool.connection() as conn:
        stored = await get_brief(conn, user_id, brief_id)
        if stored is None:
            raise _not_found()
        readiness = check_readiness(stored.brief, await load_snapshot(conn, user_id))
    return _response(stored, readiness)


@router.patch("/{brief_id}", response_model=None)
async def replace_brief(
    request: Request, user_id: UserId, key: IdempotencyKey, brief_id: uuid.UUID, body: BriefBody
) -> dict[str, Any] | JSONResponse:
    """Replace the caller's brief after review. Refused once a call used it."""
    brief = validate_brief(body)
    pool = get_pool(request)
    async with pool.connection() as conn, conn.transaction():
        replay = await claim_idempotency(conn, user_id, key)
        if replay is not None:
            return JSONResponse(status_code=replay.status_code, content=replay.body)
        stored = await get_brief(conn, user_id, brief_id, for_update=True)
        if stored is None:
            raise _not_found()
        if await brief_has_call(conn, user_id, brief_id):
            raise ApiError("brief_in_use", 409, "A call already started from this brief", False)
        readiness = check_readiness(brief, await load_snapshot(conn, user_id))
        await update_brief(conn, user_id, brief_id, brief, readiness.missing_fields)
        updated = StoredBrief(
            id=stored.id,
            brief=brief,
            missing_fields=readiness.missing_fields,
            created_at=stored.created_at,
        )
        result = _response(updated, readiness)
        await store_idempotent_response(conn, user_id, key, 200, result)
    return result

"""Brief API (PC-3 to PC-7): save the brief the pre-call chat built, read it, replace it.

- ``POST /briefs``: create. Body is a ``brief.v1`` brief. Needs ``Idempotency-Key``. 201.
- ``GET /briefs/{brief_id}``: read, with readiness checked against the profile now.
- ``PATCH /briefs/{brief_id}``: replace after the user edits the review card (PC-6). The
  body is the whole brief (the chat always sends the full reviewed brief). PATCH, not PUT,
  because the shared CORS setup in ``main.py`` allows PATCH from the browser. Needs
  ``Idempotency-Key``. 409 ``brief_in_use`` once a call was started from it.

Those three answer ``{brief_id, brief, missing_fields, opening_line, created_at}``. Start is
allowed only when ``missing_fields`` is empty; ``POST /calls`` (Coco) checks again.

Brief builder (plan Step 9), both read-only, so no ``Idempotency-Key``:

- ``GET /briefs/templates``: ``{templates: [CallTemplate, ...]}``, the P0 templates in chip
  order, in the Step 7 format (``app/briefs/catalog.py``).
- ``POST /briefs/build``: the chat's draft (``BuildRequest``) -> ``{brief, missing_fields,
  opening_line}``. Reads the caller's profile, saves nothing. ``profile_additions`` (fields the
  user typed because the profile lacked them) count for this build only, with ON-5 default
  sharing, and are never written to ``profile_fields`` (Steve's ON-7) or into the brief.
  422 ``invalid_request`` (body shape), ``unknown_template``, ``invalid_phone``.
  ``goal_summary`` (optional, custom calls only) is the summary ``/briefs/interpret`` gave, so
  rebuilding never calls the LLM again.

LLM step for custom goals (plan Step 10, PC-3), read-only, so no ``Idempotency-Key``:

- ``POST /briefs/interpret``: same body as ``/briefs/build`` with ``template_key: null``.
  Asks the LLM for a goal summary and suggestions, then builds exactly like ``/briefs/build``:
  ``{brief, missing_fields, opening_line, interpretation}`` (``interpret.Interpretation``).
  Its own route, so ``/briefs/build`` stays free and repeatable and only this call spends
  the 10-per-hour limit. Without a key, or when the answer fails validation, it answers 200
  with the user's own words and ``interpretation.llm_used: false`` plus a ``reason``.
  422 ``not_a_custom_call``; 429 ``rate_limited`` (retryable); 503 ``llm_unavailable``
  (timeout or provider down, retryable) or ``llm_misconfigured`` (not retryable). After any
  of those the chat can continue with ``/briefs/build``, which never uses the LLM.
"""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.auth import current_user
from app.briefs.builder import (
    Draft,
    ProfileField,
    build_brief,
    profile_from_snapshot,
    profile_with_additions,
    snapshot_of,
)
from app.briefs.catalog import KEY_PATTERN, get_template, templates_json
from app.briefs.interpret import interpret_custom_goal
from app.briefs.llm import GoalInterpreter, LlmError, check_goal_summary, interpreter_from_settings
from app.briefs.phone import check_phone
from app.briefs.ratelimit import reserve_llm_build
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


# Limits match the chat (frontend chat.ts): GOAL_MAX, BUSINESS_NAME_MAX, ANSWER_MAX,
# LIST_ITEM_MAX and LIST_MAX_ITEMS.
FieldKey = Annotated[str, StringConstraints(pattern=KEY_PATTERN, max_length=64)]
Answer = Annotated[str, StringConstraints(max_length=200)]


class ListItem(BaseModel):
    """One item of a PC-5 list, in the brief.v1 ``{text}`` shape."""

    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ProfileAddition(BaseModel):
    """A profile field typed in the chat. Its sharing is always the ON-5 default."""

    model_config = ConfigDict(extra="forbid")
    key: FieldKey
    value: Answer


ListItems = Annotated[list[ListItem], Field(max_length=5)]


class BuildRequest(BaseModel):
    """Body of ``POST /briefs/build``: the finished chat draft (frontend ``CallDraft``)."""

    model_config = ConfigDict(extra="forbid")
    template_key: str | None
    goal: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    business_name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    phone_e164: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    answers: Annotated[dict[FieldKey, Answer], Field(max_length=20)] = {}
    profile_additions: Annotated[list[ProfileAddition], Field(max_length=10)] = []
    constraints: ListItems = []
    preferences: ListItems = []
    fallbacks: ListItems = []
    never_agree: ListItems = []
    goal_summary: Annotated[str, AfterValidator(check_goal_summary)] | None = None


def _draft(body: BuildRequest) -> Draft:
    """Check the template and number, then turn the body into a builder ``Draft``."""
    if body.template_key is not None and get_template(body.template_key) is None:
        raise ApiError("unknown_template", 422, "template_key: no such template", False)
    if body.template_key is not None and body.goal_summary is not None:
        raise ApiError(
            "invalid_request", 422, "goal_summary: only for custom calls (template_key null)"
        )
    phone = check_phone(body.phone_e164)
    if not phone.ok or phone.e164 is None:
        raise ApiError("invalid_phone", 422, phone.message, False)
    return Draft(
        template_key=body.template_key,
        goal=body.goal,
        business_name=body.business_name,
        phone_e164=phone.e164,
        answers=dict(body.answers),
        profile_additions=[(a.key, a.value) for a in body.profile_additions],
        constraints=[i.text for i in body.constraints],
        preferences=[i.text for i in body.preferences],
        fallbacks=[i.text for i in body.fallbacks],
        never_agree=[i.text for i in body.never_agree],
        goal_summary=body.goal_summary,
    )


def build_response(draft: Draft, profile: Sequence[ProfileField]) -> dict[str, Any]:
    """Build the brief and check it: ``{brief, missing_fields, opening_line}``.

    ``missing_fields`` is the builder's list (template fields, placeholders, calling_name)
    followed by anything ``check_readiness`` adds, such as an Ask-me-first value typed into
    the brief. ``opening_line`` is set only when nothing is missing.
    """
    template = get_template(draft.template_key) if draft.template_key else None
    built = build_brief(draft, template, profile)
    snapshot = snapshot_of(profile_with_additions(profile, draft.profile_additions))
    readiness = check_readiness(built.brief, snapshot)
    missing = list(dict.fromkeys([*built.missing_fields, *readiness.missing_fields]))
    return {
        "brief": built.brief,
        "missing_fields": missing,
        "opening_line": None if missing else readiness.opening_line,
    }


@router.get("/templates")
async def list_templates(user_id: UserId) -> dict[str, Any]:
    """The call templates (PC-2), so the chat reads the same data as the builder."""
    return {"templates": templates_json()}


@router.post("/build")
async def build(request: Request, user_id: UserId, body: BuildRequest) -> dict[str, Any]:
    """Build a brief from the chat's draft against the caller's profile. Saves nothing."""
    draft = _draft(body)
    pool = get_pool(request)
    async with pool.connection() as conn:
        profile = profile_from_snapshot(await load_snapshot(conn, user_id))
    return build_response(draft, profile)


def get_goal_interpreter(request: Request) -> GoalInterpreter | None:
    """FastAPI dependency: the LLM client from settings, or None when no key is set."""
    settings = request.app.state.settings
    return interpreter_from_settings(settings)


Interpreter = Annotated[GoalInterpreter | None, Depends(get_goal_interpreter)]


@router.post("/interpret")
async def interpret(
    request: Request, user_id: UserId, body: BuildRequest, interpreter: Interpreter
) -> dict[str, Any]:
    """PC-3: build a custom brief with the LLM's goal summary and suggestions. Saves no brief.

    Only a call that will reach the LLM counts against the hourly limit. The database
    connection is released before the LLM call (up to 20 s).
    """
    if body.template_key is not None:
        raise ApiError(
            "not_a_custom_call", 422, "template_key: the AI step is only for custom calls"
        )
    draft = _draft(body)
    pool = get_pool(request)
    async with pool.connection() as conn, conn.transaction():
        if interpreter is not None:
            await reserve_llm_build(conn, user_id)
        profile = profile_from_snapshot(await load_snapshot(conn, user_id))
    try:
        interpretation = await interpret_custom_goal(draft, profile, interpreter)
    except LlmError as exc:
        raise exc.api_error() from None
    built = build_response(replace(draft, goal_summary=interpretation.goal_summary), profile)
    return {**built, "interpretation": interpretation.to_json()}


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

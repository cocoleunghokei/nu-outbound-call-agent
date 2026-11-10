"""Supabase JWT verification: asymmetric keys via a cached JWKS, HS256 via the legacy secret."""

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import jwt
from fastapi import Header, Request

from app.config import Settings
from app.errors import ApiError

ASYMMETRIC_ALGORITHMS = {"ES256", "RS256"}
JWKS_TTL_SECONDS = 600.0
JWKS_MIN_REFETCH_SECONDS = 30.0


def _unauthenticated(message: str = "Missing or invalid credentials") -> ApiError:
    return ApiError("unauthenticated", 401, message, False)


class JwksCache:
    """Async, shared cache of the project's JWKS.

    Cached for 10 minutes. An unknown kid triggers a refetch, at most once per 30 seconds,
    so key rotation works without letting bad tokens hammer the Auth server.
    """

    def __init__(
        self,
        url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._transport = transport
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(transport=self._transport, timeout=5.0) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            document = response.json()
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in document.get("keys", []):
            kid = jwk.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = jwt.PyJWK(jwk)
            except jwt.PyJWTError:
                continue
        self._keys = keys
        self._fetched_at = self._clock()

    async def get_key(self, kid: str) -> jwt.PyJWK | None:
        """Return the key for kid, or None if the JWKS does not have it."""
        async with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= JWKS_TTL_SECONDS
            if stale:
                await self._refresh()
            elif kid not in self._keys and self._may_refetch(now):
                await self._refresh()
            return self._keys.get(kid)

    def _may_refetch(self, now: float) -> bool:
        return self._fetched_at is None or now - self._fetched_at >= JWKS_MIN_REFETCH_SECONDS

    async def _refresh(self) -> None:
        try:
            await self._fetch()
        except (httpx.HTTPError, ValueError) as exc:
            raise ApiError("auth_unavailable", 503, "Cannot reach the Auth server", True) from exc


def _get_jwks_cache(request: Request, settings: Settings) -> JwksCache:
    cache: JwksCache | None = getattr(request.app.state, "jwks_cache", None)
    if cache is None:
        cache = JwksCache(settings.supabase_jwks_url)
        request.app.state.jwks_cache = cache
    return cache


async def _signing_key(request: Request, settings: Settings, token: str) -> tuple[Any, str]:
    """Pick the verification key and algorithm from the token header; reject all others."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _unauthenticated() from exc
    alg = header.get("alg")
    if alg in ASYMMETRIC_ALGORITHMS:
        kid = header.get("kid")
        if not isinstance(kid, str) or not settings.supabase_jwks_url:
            raise _unauthenticated()
        key = await _get_jwks_cache(request, settings).get_key(kid)
        if key is None:
            raise _unauthenticated()
        if key.algorithm_name != alg:
            raise _unauthenticated()
        return key.key, str(alg)
    if alg == "HS256" and settings.supabase_jwt_secret is not None:
        return settings.supabase_jwt_secret.get_secret_value(), "HS256"
    raise _unauthenticated()


async def current_user(
    request: Request, authorization: str | None = Header(default=None)
) -> uuid.UUID:
    """FastAPI dependency: verify the Supabase access token and return the user's id."""
    if authorization is None:
        raise _unauthenticated("Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise _unauthenticated("Expected 'Authorization: Bearer <token>'")

    settings: Settings = request.app.state.settings
    key, algorithm = await _signing_key(request, settings, token)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=settings.supabase_jwt_issuer,
            options={"require": ["exp", "aud", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError("token_expired", 401, "Token has expired", False) from exc
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise _unauthenticated() from exc
    try:
        return uuid.UUID(str(claims["sub"]))
    except ValueError as exc:
        raise _unauthenticated() from exc

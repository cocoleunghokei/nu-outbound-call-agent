"""Tests for Supabase JWT verification and GET /me."""

import base64
import json
import time
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import JwksCache, current_user
from app.config import Settings
from app.idempotency import idempotency_key
from app.main import create_app
from tests.conftest import SignedUpUser, SupabaseEnv

SECRET = "test-secret-with-at-least-32-characters-long"  # gitleaks:allow
ISSUER = "http://issuer.example.test/auth/v1"
USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def make_token(**overrides: Any) -> str:
    claims: dict[str, Any] = {
        "sub": str(USER_ID),
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int(time.time()) + 600,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def make_client(jwks_cache: JwksCache | None = None) -> TestClient:
    settings = Settings(
        supabase_jwt_secret=SECRET,  # type: ignore[arg-type]
        supabase_jwt_issuer=ISSUER,
        supabase_jwks_url="http://jwks.example.test/jwks.json",
    )
    app: FastAPI = create_app(settings)
    if jwks_cache is not None:
        app.state.jwks_cache = jwks_cache

    @app.get("/whoami")
    async def whoami(user_id: Annotated[uuid.UUID, Depends(current_user)]) -> dict[str, str]:
        return {"user_id": str(user_id)}

    @app.post("/needs-key")
    async def needs_key(key: Annotated[uuid.UUID, Depends(idempotency_key)]) -> dict[str, str]:
        return {"key": str(key)}

    return TestClient(app)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_unauthenticated(response: httpx.Response, code: str = "unauthenticated") -> None:
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == code
    assert error["retryable"] is False
    assert isinstance(error["message"], str)


def test_valid_hs256_token_accepted() -> None:
    response = make_client().get("/whoami", headers=bearer(make_token()))
    assert response.status_code == 200
    assert response.json() == {"user_id": str(USER_ID)}


def test_missing_header_401_shape() -> None:
    response = make_client().get("/whoami")
    assert_unauthenticated(response)
    assert set(response.json()) == {"error"}


def test_bad_scheme_401() -> None:
    client = make_client()
    assert_unauthenticated(client.get("/whoami", headers={"Authorization": "Basic abc"}))
    assert_unauthenticated(client.get("/whoami", headers={"Authorization": "Bearer"}))
    assert_unauthenticated(client.get("/whoami", headers=bearer("not-a-jwt")))


def test_expired_token_401() -> None:
    token = make_token(exp=int(time.time()) - 60)
    assert_unauthenticated(make_client().get("/whoami", headers=bearer(token)), "token_expired")


def test_wrong_audience_401() -> None:
    token = make_token(aud="anon")
    assert_unauthenticated(make_client().get("/whoami", headers=bearer(token)))


def test_wrong_issuer_401() -> None:
    token = make_token(iss="http://evil.example.test/auth/v1")
    assert_unauthenticated(make_client().get("/whoami", headers=bearer(token)))


def test_non_uuid_sub_401() -> None:
    assert_unauthenticated(make_client().get("/whoami", headers=bearer(make_token(sub="alex"))))


def test_wrong_secret_401() -> None:
    token = jwt.encode(
        {"sub": str(USER_ID), "aud": "authenticated", "iss": ISSUER, "exp": time.time() + 60},
        "another-secret-with-at-least-32-characters",
        algorithm="HS256",
    )
    assert_unauthenticated(make_client().get("/whoami", headers=bearer(token)))


def test_alg_none_rejected() -> None:
    token = jwt.encode(
        {"sub": str(USER_ID), "aud": "authenticated", "iss": ISSUER, "exp": time.time() + 60},
        key=None,  # type: ignore[arg-type]
        algorithm="none",
    )
    assert_unauthenticated(make_client().get("/whoami", headers=bearer(token)))


class FakeJwks:
    """Serves a mutable JWKS over httpx.MockTransport and counts fetches."""

    def __init__(self) -> None:
        self.keys: list[dict[str, Any]] = []
        self.fetches = 0
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.fetches += 1
        return httpx.Response(200, json={"keys": self.keys})

    def add_key(self, kid: str) -> ec.EllipticCurvePrivateKey:
        private = ec.generate_private_key(ec.SECP256R1())
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
        jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
        self.keys.append(jwk)
        return private


def es256_token(private: ec.EllipticCurvePrivateKey, kid: str) -> str:
    return jwt.encode(
        {
            "sub": str(USER_ID),
            "aud": "authenticated",
            "iss": ISSUER,
            "exp": int(time.time()) + 600,
        },
        private,
        algorithm="ES256",
        headers={"kid": kid},
    )


def jwks_cache(fake: FakeJwks, clock: Callable[[], float] = time.monotonic) -> JwksCache:
    return JwksCache("http://jwks.example.test/jwks.json", transport=fake.transport, clock=clock)


def test_es256_token_verified_via_jwks() -> None:
    fake = FakeJwks()
    private = fake.add_key("key-1")
    client = make_client(jwks_cache(fake))
    response = client.get("/whoami", headers=bearer(es256_token(private, "key-1")))
    assert response.status_code == 200
    assert response.json() == {"user_id": str(USER_ID)}
    # Second request is served from cache.
    client.get("/whoami", headers=bearer(es256_token(private, "key-1")))
    assert fake.fetches == 1


def test_es256_token_signed_by_other_key_rejected() -> None:
    fake = FakeJwks()
    fake.add_key("key-1")
    attacker = ec.generate_private_key(ec.SECP256R1())
    client = make_client(jwks_cache(fake))
    assert_unauthenticated(client.get("/whoami", headers=bearer(es256_token(attacker, "key-1"))))


def test_unknown_kid_triggers_one_refetch() -> None:
    now = [1000.0]
    fake = FakeJwks()
    old = fake.add_key("old")
    client = make_client(jwks_cache(fake, clock=lambda: now[0]))
    assert client.get("/whoami", headers=bearer(es256_token(old, "old"))).status_code == 200
    assert fake.fetches == 1

    # Key rotation: a new kid appears. Enough time has passed, so we refetch once.
    new = fake.add_key("new")
    now[0] += 31
    assert client.get("/whoami", headers=bearer(es256_token(new, "new"))).status_code == 200
    assert fake.fetches == 2

    # Another unknown kid inside the 30 s window does not refetch again.
    ghost = ec.generate_private_key(ec.SECP256R1())
    now[0] += 5
    assert_unauthenticated(client.get("/whoami", headers=bearer(es256_token(ghost, "ghost"))))
    assert fake.fetches == 2


def test_alg_key_mismatch_401() -> None:
    fake = FakeJwks()
    fake.add_key("key-1")  # an ES256 JWK
    client = make_client(jwks_cache(fake))
    forged = jwt.encode(
        {"sub": str(USER_ID), "aud": "authenticated", "iss": ISSUER, "exp": time.time() + 60},
        SECRET,
        algorithm="HS256",
        headers={"kid": "key-1"},
    )
    header, payload, signature = forged.split(".")
    bad_header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT", "kid": "key-1"}).encode()
    ).rstrip(b"=")
    token = f"{bad_header.decode()}.{payload}.{signature}"
    assert_unauthenticated(client.get("/whoami", headers=bearer(token)))


def test_hs256_rejected_when_no_secret_configured() -> None:
    settings = Settings(supabase_jwt_issuer=ISSUER)
    app = create_app(settings)

    @app.get("/whoami")
    async def whoami(user_id: Annotated[uuid.UUID, Depends(current_user)]) -> dict[str, str]:
        return {"user_id": str(user_id)}

    assert_unauthenticated(TestClient(app).get("/whoami", headers=bearer(make_token())))


def test_missing_idempotency_key_400() -> None:
    client = make_client()
    for headers in ({}, {"Idempotency-Key": "not-a-uuid"}):
        response = client.post("/needs-key", headers=headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "idempotency_key_required"
    key = str(uuid.uuid4())
    ok = client.post("/needs-key", headers={"Idempotency-Key": key})
    assert ok.json() == {"key": key}


@pytest.mark.db
def test_real_local_supabase_token_accepted(
    supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    """A token issued by the real local Auth server verifies, whatever algorithm the stack uses."""
    settings = Settings(
        database_url=supabase_env.db_url,
        supabase_jwt_secret=supabase_env.jwt_secret,  # type: ignore[arg-type]
        supabase_jwt_issuer=supabase_env.jwt_issuer,
        supabase_jwks_url=f"{supabase_env.auth_url}/.well-known/jwks.json",
    )
    with TestClient(create_app(settings)) as client:
        headers = bearer(signed_up_user.access_token)
        response = client.get("/me", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"user_id": str(signed_up_user.user_id), "onboarded": False}
        # Second call finds the row it created.
        assert client.get("/me", headers=headers).json()["onboarded"] is False
        assert client.get("/me").status_code == 401

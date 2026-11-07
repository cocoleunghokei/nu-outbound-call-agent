"""Find the local Supabase stack. Shared by the dev seed and the db tests."""

import os
import subprocess
from dataclasses import dataclass

ENV_NAMES = ("SUPABASE_DB_URL", "SUPABASE_AUTH_URL", "SUPABASE_JWT_SECRET", "SUPABASE_JWT_ISSUER")


@dataclass(frozen=True)
class SupabaseEnv:
    """Where the local Postgres and Auth API live."""

    db_url: str
    auth_url: str  # Auth API root, e.g. http://127.0.0.1:54321/auth/v1
    jwt_secret: str
    jwt_issuer: str


def _status_env() -> dict[str, str]:
    """Parse `supabase status -o env`; raise a clear error if it cannot run."""
    try:
        out = subprocess.run(
            ["supabase", "status", "-o", "env"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "need SUPABASE_DB_URL, SUPABASE_AUTH_URL, SUPABASE_JWT_SECRET and "
            "SUPABASE_JWT_ISSUER, or a running `supabase start`; neither was found"
        ) from exc
    values: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            name, _, raw = line.partition("=")
            values[name.strip()] = raw.strip().strip('"')
    return values


def discover_supabase_env() -> SupabaseEnv:
    """Read the SUPABASE_* variables, falling back to `supabase status -o env`."""
    env = {name: os.environ.get(name, "") for name in ENV_NAMES}
    if not all(env.values()):
        status = _status_env()
        api_url = status.get("API_URL", "").rstrip("/")
        env["SUPABASE_DB_URL"] = env["SUPABASE_DB_URL"] or status.get("DB_URL", "")
        env["SUPABASE_AUTH_URL"] = env["SUPABASE_AUTH_URL"] or f"{api_url}/auth/v1"
        env["SUPABASE_JWT_SECRET"] = env["SUPABASE_JWT_SECRET"] or status.get("JWT_SECRET", "")
        env["SUPABASE_JWT_ISSUER"] = env["SUPABASE_JWT_ISSUER"] or f"{api_url}/auth/v1"
    missing = [name for name, value in env.items() if not value]
    if missing:
        raise RuntimeError(f"cannot find: {', '.join(missing)}")
    return SupabaseEnv(
        db_url=env["SUPABASE_DB_URL"],
        auth_url=env["SUPABASE_AUTH_URL"].rstrip("/"),
        jwt_secret=env["SUPABASE_JWT_SECRET"],
        jwt_issuer=env["SUPABASE_JWT_ISSUER"],
    )

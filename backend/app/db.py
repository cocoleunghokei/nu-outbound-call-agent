"""Async Postgres connection pool, opened in the app lifespan."""

from fastapi import Request
from psycopg_pool import AsyncConnectionPool

from app.errors import ApiError


def create_pool(database_url: str) -> AsyncConnectionPool:
    """Build a pool that is not yet open; the lifespan opens it.

    The URL is the Supabase session-pooler connection string. The backend connects as the
    database owner, so row-level security does not apply to it.
    """
    return AsyncConnectionPool(database_url, min_size=1, max_size=10, open=False)


def get_pool(request: Request) -> AsyncConnectionPool:
    """FastAPI dependency: the pool opened in the lifespan.

    The pool exists only when DATABASE_URL is set; without it routes that need the database
    answer 503 instead of the app refusing to start (tests run without a database).
    """
    pool: AsyncConnectionPool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise ApiError("database_unavailable", 503, "Database is not configured", True)
    return pool

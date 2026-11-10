"""Transaction guards shared by the helpers that must run inside a caller's transaction."""

from typing import Any

from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus


def require_transaction(conn: AsyncConnection[Any], what: str) -> None:
    """Raise RuntimeError if ``conn`` is in autocommit mode with no transaction open.

    In that state a row lock is released at once and each statement commits alone, so the
    helper ``what`` would silently lose its atomicity. Non-autocommit connections always run
    inside a transaction (psycopg opens one implicitly).
    """
    if conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE:
        raise RuntimeError(
            f"{what} needs an open transaction (use `async with conn.transaction()`)"
        )

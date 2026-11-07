"""Shared error shape: {"error": {"code", "message", "retryable"}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An error with a stable machine-readable code, rendered in the shared shape."""

    def __init__(self, code: str, status: int, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.retryable = retryable


def error_response(code: str, status: int, message: str, retryable: bool) -> JSONResponse:
    """Build a JSON response in the shared error shape."""
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "retryable": retryable}},
    )


def _http_code(status: int) -> str:
    if status == 404:
        return "not_found"
    if status == 405:
        return "method_not_allowed"
    return f"http_{status}"


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.code, exc.status, exc.message, exc.retryable)


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = (
        f"{location}: {first.get('msg', 'invalid request')}" if location else "Invalid request"
    )
    return error_response("invalid_request", 422, message, False)


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    response = error_response(_http_code(exc.status_code), exc.status_code, str(exc.detail), False)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


class UnhandledErrorMiddleware(BaseHTTPMiddleware):
    """Turn any unhandled exception into the shared 500 shape.

    Must sit inside CORSMiddleware: Starlette's own catch-all handler runs outside it,
    so its 500s would carry no CORS headers and browsers would report a CORS failure.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception:
            logger.exception("unhandled error on %s %s", request.method, request.url.path)
            return error_response("internal", 500, "Internal server error", True)


def install_error_handlers(app: FastAPI) -> None:
    """Register the three handlers that render every error in the shared shape."""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)

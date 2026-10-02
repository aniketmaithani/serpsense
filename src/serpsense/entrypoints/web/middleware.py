"""HTTP middleware: request ids, access logging and security headers."""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from serpsense.observability import get_logger

log = get_logger(__name__)

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; "
    "object-src 'none'"
)
HSTS_VALUE = "max-age=31536000; includeSubDomains"
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
UNMATCHED_ROUTE = "unmatched"

CallNext = Callable[[Request], Awaitable[Response]]


def apply_security_headers(response: Response, *, hsts: bool) -> None:
    """Security headers required by AGENTS.md §6; also used by the 500 handler."""
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = PERMISSIONS_POLICY
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if hsts:
        response.headers["Strict-Transport-Security"] = HSTS_VALUE


def route_template(request: Request) -> str:
    """The matched route's template (e.g. `/brands/{brand_id}`), never the raw path."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a fresh request id to the log context and emit one access log per request."""

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        request_id = uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "http.request_failed", method=request.method, route=route_template(request)
            )
            raise
        response.headers["X-Request-ID"] = request_id
        log.info(
            "http.request_completed",
            method=request.method,
            route=route_template(request),
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        response = await call_next(request)
        apply_security_headers(response, hsts=self._hsts)
        return response

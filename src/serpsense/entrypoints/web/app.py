"""FastAPI application factory.

Run with `uvicorn serpsense.entrypoints.web.app:create_app --factory`.
"""

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from serpsense.composition import Container, build_container
from serpsense.entrypoints.web.auth import router as auth_router
from serpsense.entrypoints.web.brands import router as brands_router
from serpsense.entrypoints.web.health import router as health_router
from serpsense.entrypoints.web.middleware import (
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    apply_security_headers,
    route_template,
)
from serpsense.entrypoints.web.pages import STATIC
from serpsense.observability import get_logger

log = get_logger(__name__)


def create_app(container: Container | None = None) -> FastAPI:
    resolved = container if container is not None else build_container()
    hsts = resolved.settings.is_production
    app = FastAPI(title="SerpSense", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = resolved
    app.add_middleware(SecurityHeadersMiddleware, hsts=hsts)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(brands_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(Exception)
    def internal_error(request: Request, exc: Exception) -> PlainTextResponse:
        # Runs outside the middleware stack, so headers are applied here too. The exception is
        # logged once, here (scrubbed); the client gets no details.
        log.error(
            "http.request_failed",
            method=request.method,
            route=route_template(request),
            exc_info=exc,
        )
        response = PlainTextResponse("Internal Server Error", status_code=500)
        apply_security_headers(response, hsts=hsts)
        return response

    return app

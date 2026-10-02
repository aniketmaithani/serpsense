"""FastAPI application factory.

Run with `uvicorn serpsense.entrypoints.web.app:create_app --factory`.
"""

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from serpsense.composition import Container, build_container
from serpsense.entrypoints.web.health import router as health_router
from serpsense.entrypoints.web.middleware import (
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    apply_security_headers,
)


def create_app(container: Container | None = None) -> FastAPI:
    resolved = container if container is not None else build_container()
    hsts = resolved.settings.is_production
    app = FastAPI(title="SerpSense", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = resolved
    app.add_middleware(SecurityHeadersMiddleware, hsts=hsts)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)

    @app.exception_handler(Exception)
    def internal_error(_: Request, __: Exception) -> PlainTextResponse:
        # Runs outside the middleware stack, so headers are applied here too.
        # The exception itself is logged by RequestContextMiddleware; no details leak to the client.
        response = PlainTextResponse("Internal Server Error", status_code=500)
        apply_security_headers(response, hsts=hsts)
        return response

    return app

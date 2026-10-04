"""FastAPI application factory.

Run with `uvicorn serpsense.entrypoints.web.app:create_app --factory`.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from serpsense.composition import Container, build_container
from serpsense.entrypoints.web.account import router as account_router
from serpsense.entrypoints.web.add_brand import router as add_brand_router
from serpsense.entrypoints.web.ai_settings import router as ai_router
from serpsense.entrypoints.web.auth import router as auth_router
from serpsense.entrypoints.web.brand_settings import router as settings_router
from serpsense.entrypoints.web.brands import router as brands_router
from serpsense.entrypoints.web.console import router as console_router
from serpsense.entrypoints.web.crisis_tuning import router as crisis_tuning_router
from serpsense.entrypoints.web.health import router as health_router
from serpsense.entrypoints.web.middleware import (
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    apply_security_headers,
    route_template,
)
from serpsense.entrypoints.web.notifications import router as notifications_router
from serpsense.entrypoints.web.pages import STATIC
from serpsense.entrypoints.web.stories import router as stories_router
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
    app.include_router(add_brand_router)  # before /brands/{id}, which would take 'new'
    app.include_router(brands_router)
    app.include_router(crisis_tuning_router)
    app.include_router(notifications_router)
    app.include_router(settings_router)
    app.include_router(stories_router)
    app.include_router(account_router)
    app.include_router(ai_router)
    app.include_router(console_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(RequestValidationError)
    def invalid_form(request: Request, exc: RequestValidationError) -> PlainTextResponse:
        # A tampered or incomplete form: say so without echoing what was sent (it carries the
        # CSRF token); an expected outcome, not an error.
        response = PlainTextResponse("That form wasn't valid. Go back and try again.", 400)
        response.headers["Cache-Control"] = "no-store"
        apply_security_headers(response, hsts=hsts)
        return response

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

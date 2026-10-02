"""FastAPI application factory.

Run with `uvicorn serpsense.entrypoints.web.app:create_app --factory`.
"""

from fastapi import FastAPI

from serpsense.composition import Container, build_container
from serpsense.entrypoints.web.health import router as health_router
from serpsense.entrypoints.web.middleware import (
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)


def create_app(container: Container | None = None) -> FastAPI:
    resolved = container if container is not None else build_container()
    app = FastAPI(title="SerpSense", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = resolved
    app.add_middleware(SecurityHeadersMiddleware, hsts=resolved.settings.is_production)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    return app

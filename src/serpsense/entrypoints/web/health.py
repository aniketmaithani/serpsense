"""Liveness and readiness endpoints (ADR-0012).

Readiness details (which dependency is down) are logged, not returned, so the public
endpoint doesn't describe internal infrastructure.
"""

from typing import Literal, cast

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from serpsense.composition import Container
from serpsense.observability import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


class HealthStatus(BaseModel):
    status: Literal["ok", "unavailable"]


def _container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


@router.get("/healthz")
def healthz() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/readyz")
def readyz(request: Request, response: Response) -> HealthStatus:
    failed = [check.name for check in _container(request).health_checks if not check.check()]
    if failed:
        log.warning("health.not_ready", failed_dependencies=failed)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(status="unavailable")
    return HealthStatus(status="ok")

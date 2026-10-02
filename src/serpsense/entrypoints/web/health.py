"""Liveness and readiness endpoints (ADR-0012)."""

from typing import cast

from fastapi import APIRouter, Request, Response, status

from serpsense.composition import Container

router = APIRouter(tags=["health"])


def _container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, object]:
    results = {check.name: check.check() for check in _container(request).health_checks}
    ready = all(results.values())
    response.status_code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "unavailable", "checks": results}

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from serpsense.composition import Container
from serpsense.config import Settings
from serpsense.entrypoints.web.app import create_app

pytestmark = pytest.mark.api


@dataclass(frozen=True)
class FakeCheck:
    name: str
    healthy: bool

    def check(self) -> bool:
        return self.healthy


def client_with(settings: Settings, *checks: FakeCheck) -> TestClient:
    return TestClient(create_app(Container(settings=settings, health_checks=checks)))


def test_healthz_returns_ok(settings: Settings) -> None:
    response = client_with(settings).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_200_when_all_dependencies_healthy(settings: Settings) -> None:
    client = client_with(settings, FakeCheck("postgres", True), FakeCheck("redis", True))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": True, "redis": True}}


def test_readyz_returns_503_when_a_dependency_is_down(settings: Settings) -> None:
    client = client_with(settings, FakeCheck("postgres", True), FakeCheck("redis", False))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] is False


def test_responses_carry_security_headers_and_request_id(settings: Settings) -> None:
    response = client_with(settings).get("/healthz")
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert len(response.headers["X-Request-ID"]) == 32
    assert "Strict-Transport-Security" not in response.headers


def test_api_docs_are_not_exposed(settings: Settings) -> None:
    client = client_with(settings)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from serpsense.composition import Container
from serpsense.config import Settings
from serpsense.entrypoints.web.app import create_app
from tests.factories import make_settings

pytestmark = pytest.mark.api

PRODUCTION = {
    "app_env": "production",
    "base_url": "https://serpsense.ai",
    "signup_mode": "invite",
    "smtp_starttls": "true",
    "database_url": "postgresql+psycopg://app:prod-pw@db.internal:5432/serpsense",
}


@dataclass(frozen=True)
class FakeCheck:
    name: str
    healthy: bool

    def check(self) -> bool:
        return self.healthy


def client_with(settings: Settings, *checks: FakeCheck) -> TestClient:
    app = create_app(Container(settings=settings, health_checks=checks))
    return TestClient(app, raise_server_exceptions=False)


def test_healthz_returns_ok(settings: Settings) -> None:
    response = client_with(settings).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_200_when_all_dependencies_healthy(settings: Settings) -> None:
    client = client_with(settings, FakeCheck("postgres", True), FakeCheck("redis", True))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_503_without_naming_the_failed_dependency(settings: Settings) -> None:
    client = client_with(settings, FakeCheck("postgres", True), FakeCheck("redis", False))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "redis" not in response.text


def test_responses_carry_security_headers_and_request_id(settings: Settings) -> None:
    response = client_with(settings).get("/healthz")
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]
    assert len(response.headers["X-Request-ID"]) == 32
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_is_sent_in_production() -> None:
    response = client_with(make_settings(**PRODUCTION)).get("/healthz")
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def test_unhandled_errors_return_generic_500_with_security_headers(settings: Settings) -> None:
    client = client_with(settings)

    @client.app.get("/boom")  # type: ignore[attr-defined]  # TestClient.app is the FastAPI app
    def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    response = client.get("/boom")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_api_docs_are_not_exposed(settings: Settings) -> None:
    client = client_with(settings)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404

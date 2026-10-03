"""Test data builders shared across test layers."""

from serpsense.composition import Container, build_celery, build_session_guard, build_sign_in
from serpsense.config import Settings
from serpsense.ports.health import HealthCheck

# Obviously fake, low-entropy values: never real secrets.
TEST_SECRET_KEY = "unit-test-only-" + "x" * 32
TEST_FERNET_KEY = "A" * 43 + "="


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "secret_key": TEST_SECRET_KEY,
        "outbox_encryption_keys": TEST_FERNET_KEY,
        "database_url": "postgresql+psycopg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def make_container(settings: Settings, checks: tuple[HealthCheck, ...] = ()) -> Container:
    """A web container whose services connect to nothing until a request uses them."""
    celery = build_celery(settings)
    sign_in, sessions = build_sign_in(settings, celery), build_session_guard(settings, celery)
    return Container(settings, checks, sign_in, sessions)

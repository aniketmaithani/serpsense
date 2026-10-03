"""Test data builders shared across test layers."""

from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.inbox import SqlInbox
from serpsense.adapters.db.overview import SqlOverview
from serpsense.adapters.system_clock import SystemClock
from serpsense.composition import (
    Container,
    build_brand_settings,
    build_celery,
    build_scan_now,
    build_session_guard,
    build_sign_in,
)
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
    engine = create_db_engine(settings.database_url.get_secret_value())
    return Container(
        settings=settings,
        health_checks=checks,
        sign_in=sign_in,
        sessions=sessions,
        overview=SqlOverview(engine.connect),
        scan_now=build_scan_now(settings, engine, celery),
        inbox=SqlInbox(engine, SystemClock()),
        brand_settings=build_brand_settings(settings, engine, celery),
    )

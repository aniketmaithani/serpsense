import pytest

from serpsense.composition import build_celery, build_container
from serpsense.config import Settings

pytestmark = pytest.mark.unit


def test_build_container_wires_postgres_and_redis_checks_without_connecting(
    settings: Settings,
) -> None:
    container = build_container(settings)
    assert container.settings is settings
    assert [check.name for check in container.health_checks] == ["postgres", "redis"]


def test_build_celery_uses_redis_broker(settings: Settings) -> None:
    celery = build_celery(settings)
    assert celery.conf.broker_url == settings.redis_url.get_secret_value()

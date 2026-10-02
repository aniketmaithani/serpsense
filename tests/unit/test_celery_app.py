import importlib

import pytest
from celery.signals import setup_logging

from serpsense.adapters.jobs.celery_factory import HEARTBEAT_TASK
from tests.factories import TEST_FERNET_KEY, TEST_SECRET_KEY

pytestmark = pytest.mark.unit


@pytest.mark.usefixtures("restore_logging")
def test_celery_entrypoint_registers_heartbeat_and_owns_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "SECRET_KEY": TEST_SECRET_KEY,
        "OUTBOX_ENCRYPTION_KEYS": TEST_FERNET_KEY,
        "DATABASE_URL": "postgresql+psycopg://t:t@localhost:5432/t",
        "REDIS_URL": "redis://localhost:6379/0",
        "APP_ENV": "test",
    }.items():
        monkeypatch.setenv(name, value)
    module = importlib.import_module("serpsense.entrypoints.jobs.celery_app")

    # The beat schedule and the registered task must agree, or the heartbeat silently stops.
    assert module.app.conf.beat_schedule["heartbeat"]["task"] == HEARTBEAT_TASK
    assert HEARTBEAT_TASK in module.app.tasks
    # A setup_logging receiver stops Celery from installing its own unscrubbed handlers.
    assert any(receiver for receiver in setup_logging.receivers)
    module.heartbeat()

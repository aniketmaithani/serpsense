import importlib
import uuid
from types import ModuleType, SimpleNamespace

import pytest
import structlog
from celery.signals import setup_logging

from serpsense.adapters.jobs.celery_factory import (
    DISPATCH_TASK,
    HEARTBEAT_TASK,
    OUTBOX_TASK,
    RUN_SCAN_TASK,
    SCAN_TIME_LIMIT_SECONDS,
    SWEEP_TASK,
)
from tests.factories import TEST_FERNET_KEY, TEST_SECRET_KEY

pytestmark = pytest.mark.unit


def entrypoint(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    for name, value in {
        "SECRET_KEY": TEST_SECRET_KEY,
        "OUTBOX_ENCRYPTION_KEYS": TEST_FERNET_KEY,
        "DATABASE_URL": "postgresql+psycopg://t:t@localhost:5432/t",
        "REDIS_URL": "redis://localhost:6379/0",
        "APP_ENV": "test",
    }.items():
        monkeypatch.setenv(name, value)
    return importlib.import_module("serpsense.entrypoints.jobs.celery_app")


@pytest.mark.usefixtures("restore_logging")
def test_celery_entrypoint_registers_heartbeat_and_owns_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = entrypoint(monkeypatch)

    # The beat schedule and the registered task must agree, or the heartbeat silently stops.
    assert module.app.conf.beat_schedule["heartbeat"]["task"] == HEARTBEAT_TASK
    assert HEARTBEAT_TASK in module.app.tasks
    # Our receiver must be connected: it stops Celery from installing unscrubbed handlers.
    responders = [receiver for receiver, _ in setup_logging.send(sender=None)]
    assert module._keep_our_logging in responders
    module.heartbeat()


@pytest.mark.usefixtures("restore_logging")
def test_every_scheduled_task_is_registered_and_the_scan_task_has_the_scan_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = entrypoint(monkeypatch)
    scheduled = {entry["task"] for entry in module.app.conf.beat_schedule.values()}
    expected = {HEARTBEAT_TASK, DISPATCH_TASK, SWEEP_TASK, OUTBOX_TASK}
    assert scheduled == expected <= set(module.app.tasks)
    scan_task = module.app.tasks[RUN_SCAN_TASK]
    assert (scan_task.time_limit, scan_task.soft_time_limit) == (SCAN_TIME_LIMIT_SECONDS, None)
    assert module.app.conf.task_soft_time_limit is None  # nor does any other task get one


@pytest.mark.usefixtures("restore_logging")
def test_the_tasks_hand_their_work_to_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    module = entrypoint(monkeypatch)
    calls: list[object] = []

    def run(scan_id: uuid.UUID) -> None:
        calls.append((scan_id, structlog.contextvars.get_contextvars().get("scan_id")))

    worker = SimpleNamespace(
        scans=SimpleNamespace(run=run),
        dispatcher=SimpleNamespace(dispatch=lambda: calls.append("dispatched")),
        sweeper=SimpleNamespace(sweep=lambda: calls.append("swept")),
    )
    monkeypatch.setattr(module, "_worker", lambda: worker)
    outbox = SimpleNamespace(dispatch=lambda: calls.append("emailed"))
    monkeypatch.setattr(module, "_outbox", lambda: outbox)
    scan_id = uuid.uuid4()
    module.run_scan(str(scan_id))
    module.dispatch_due_scans()
    module.sweep_stuck_work()
    module.dispatch_outbox()
    assert calls == [(scan_id, str(scan_id)), "dispatched", "swept", "emailed"]

import pytest

from serpsense.adapters.jobs.celery_factory import (
    HEARTBEAT_TASK,
    QUEUES,
    SCAN_TIME_LIMIT_SECONDS,
    VISIBILITY_TIMEOUT_SECONDS,
    create_celery,
)

pytestmark = pytest.mark.unit


def test_celery_config_matches_adr_0005() -> None:
    conf = create_celery("redis://localhost:6379/0").conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.task_ignore_result is True
    assert conf.result_backend is None
    assert conf.worker_prefetch_multiplier == 1
    assert conf.worker_hijack_root_logger is False
    assert {q.name for q in conf.task_queues} == set(QUEUES)


def test_visibility_timeout_exceeds_largest_task_limit() -> None:
    conf = create_celery("redis://localhost:6379/0").conf
    assert conf.broker_transport_options["visibility_timeout"] == VISIBILITY_TIMEOUT_SECONDS
    assert VISIBILITY_TIMEOUT_SECONDS > SCAN_TIME_LIMIT_SECONDS >= conf.task_time_limit


@pytest.mark.parametrize(
    ("task", "queue"),
    [
        ("serpsense.scans.run", "scans"),
        ("serpsense.outbox.dispatch", "outbox"),
        ("serpsense.maintenance.sweep", "maintenance"),
    ],
)
def test_tasks_are_routed_by_name_prefix(task: str, queue: str) -> None:
    celery = create_celery("redis://localhost:6379/0")
    assert celery.amqp.router.route({}, task)["queue"].name == queue


def test_beat_schedules_heartbeat() -> None:
    schedule = create_celery("redis://localhost:6379/0").conf.beat_schedule
    assert schedule["heartbeat"]["task"] == HEARTBEAT_TASK


def test_each_queue_has_its_own_exchange_and_routing_key() -> None:
    queues = create_celery("redis://localhost:6379/0").conf.task_queues
    bindings = {(q.exchange.name, q.routing_key) for q in queues}
    assert bindings == {(name, name) for name in QUEUES}

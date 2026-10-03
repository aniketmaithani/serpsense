"""The JobQueue port on Celery: a message names the scan, and a broker failure is reported."""

import uuid
from typing import Any

import pytest
from kombu.exceptions import OperationalError

from serpsense.adapters.jobs.celery_factory import (
    EXPLAIN_TASK,
    OUTBOX_TASK,
    RUN_SCAN_TASK,
    create_celery,
)
from serpsense.adapters.jobs.celery_queue import CeleryJobQueue
from serpsense.ports.job_queue import JobQueueUnavailable

pytestmark = pytest.mark.unit


class Broker:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.error = error

    def send_task(self, name: str, args: list[str], expires: float | None = None) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((name, args) if expires is None else (name, args, expires))


def test_a_scan_is_nudged_by_its_id_alone() -> None:
    broker, scan_id = Broker(), uuid.uuid4()
    CeleryJobQueue(broker).run_scan(scan_id)  # type: ignore[arg-type]  # a stand-in app
    assert broker.sent == [(RUN_SCAN_TASK, [str(scan_id)])]


@pytest.mark.parametrize("error", [OperationalError("down"), ConnectionRefusedError()])
def test_a_broker_that_refuses_the_message_is_reported(error: Exception) -> None:
    queue = CeleryJobQueue(Broker(error))  # type: ignore[arg-type]  # a stand-in app
    with pytest.raises(JobQueueUnavailable) as raised:
        queue.run_scan(uuid.uuid4())
    assert raised.value.__cause__ is error


def test_the_outbox_is_nudged_with_no_arguments() -> None:
    broker = Broker()
    CeleryJobQueue(broker).dispatch_outbox()  # type: ignore[arg-type]  # a stand-in app
    assert broker.sent == [(OUTBOX_TASK, [], 15.0)]  # expires by Beat's next run
    with pytest.raises(JobQueueUnavailable):
        CeleryJobQueue(Broker(OperationalError("down"))).dispatch_outbox()  # type: ignore[arg-type]  # a stand-in app


def test_the_scan_task_is_routed_to_the_scans_queue() -> None:
    celery = create_celery("redis://localhost:6379/0")
    assert celery.amqp.router.route({}, RUN_SCAN_TASK)["queue"].name == "scans"


def test_an_alert_is_explained_on_the_scans_queue_by_its_id_alone() -> None:
    broker, alert_id = Broker(), uuid.uuid4()
    CeleryJobQueue(broker).explain_alert(alert_id)  # type: ignore[arg-type]  # a stand-in app
    assert broker.sent == [(EXPLAIN_TASK, [str(alert_id)])]
    celery = create_celery("redis://localhost:6379/0")
    assert celery.amqp.router.route({}, EXPLAIN_TASK)["queue"].name == "scans"

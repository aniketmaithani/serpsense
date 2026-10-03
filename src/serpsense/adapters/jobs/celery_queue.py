"""The JobQueue port on Celery (ADR-0005).

A message only names the job; Postgres holds everything else, and a worker re-reads it and claims
the job with a compare-and-set. A message the broker refuses is reported as JobQueueUnavailable,
and the sweep sends it again.
"""

import uuid

from celery import Celery
from kombu.exceptions import OperationalError

from serpsense.adapters.jobs.celery_factory import (
    EXPLAIN_TASK,
    OUTBOX_INTERVAL,
    OUTBOX_TASK,
    RUN_SCAN_TASK,
)
from serpsense.ports.job_queue import JobQueueUnavailable


class CeleryJobQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def run_scan(self, scan_id: uuid.UUID) -> None:
        self._send(RUN_SCAN_TASK, [str(scan_id)])

    def dispatch_outbox(self) -> None:
        # A nudge left waiting past Beat's next run is pointless; let it expire.
        self._send(OUTBOX_TASK, [], expires=OUTBOX_INTERVAL.total_seconds())

    def explain_alert(self, alert_id: uuid.UUID) -> None:
        self._send(EXPLAIN_TASK, [str(alert_id)])

    def _send(self, task: str, args: list[str], expires: float | None = None) -> None:
        try:
            self._app.send_task(task, args=args, expires=expires)
        except (OperationalError, OSError) as exc:  # the broker or its socket
            raise JobQueueUnavailable(type(exc).__name__) from exc

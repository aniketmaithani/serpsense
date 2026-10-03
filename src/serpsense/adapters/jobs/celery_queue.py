"""The JobQueue port on Celery (ADR-0005).

A message only names the job; Postgres holds everything else, and a worker re-reads it and claims
the job with a compare-and-set. A message the broker refuses is reported as JobQueueUnavailable,
and the sweep sends it again.
"""

import uuid

from celery import Celery
from kombu.exceptions import OperationalError

from serpsense.adapters.jobs.celery_factory import RUN_SCAN_TASK
from serpsense.ports.job_queue import JobQueueUnavailable


class CeleryJobQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def run_scan(self, scan_id: uuid.UUID) -> None:
        try:
            self._app.send_task(RUN_SCAN_TASK, args=[str(scan_id)])
        except (OperationalError, OSError) as exc:  # the broker or its socket
            raise JobQueueUnavailable(type(exc).__name__) from exc

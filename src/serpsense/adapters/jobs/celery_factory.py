"""Celery configuration (ADR-0005).

Shared by the worker/beat entrypoint and, later, the JobQueue adapter.

Postgres is the source of truth; Celery messages are only nudges.
"""

from datetime import timedelta

from celery import Celery
from kombu import Exchange, Queue

SCANS_QUEUE = "scans"
OUTBOX_QUEUE = "outbox"
MAINTENANCE_QUEUE = "maintenance"
QUEUES = (SCANS_QUEUE, OUTBOX_QUEUE, MAINTENANCE_QUEUE)

SCAN_TIME_LIMIT_SECONDS = 15 * 60
DEFAULT_TIME_LIMIT_SECONDS = 2 * 60
# Must exceed the largest task time limit so unacked messages aren't redelivered mid-run.
VISIBILITY_TIMEOUT_SECONDS = 2 * SCAN_TIME_LIMIT_SECONDS

HEARTBEAT_TASK = "serpsense.maintenance.heartbeat"
HEARTBEAT_INTERVAL = timedelta(seconds=60)
BEAT_SCHEDULE_FILE = "/tmp/celerybeat-schedule"  # noqa: S108 - container-local scratch file

# Tasks are routed by name prefix, so a task can't silently land on the wrong queue.
TASK_ROUTES = {
    "serpsense.scans.*": {"queue": SCANS_QUEUE},
    "serpsense.outbox.*": {"queue": OUTBOX_QUEUE},
    "serpsense.maintenance.*": {"queue": MAINTENANCE_QUEUE},
}


def create_celery(broker_url: str) -> Celery:
    celery = Celery("serpsense", broker=broker_url)
    celery.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_ignore_result=True,
        result_backend=None,
        task_time_limit=DEFAULT_TIME_LIMIT_SECONDS,
        task_soft_time_limit=DEFAULT_TIME_LIMIT_SECONDS - 15,
        worker_prefetch_multiplier=1,
        worker_hijack_root_logger=False,
        broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT_SECONDS},
        broker_connection_retry_on_startup=True,
        # Each queue gets its own direct exchange and routing key; a shared default would
        # bind every queue to the same key and deliver one message to all of them.
        task_queues=[
            Queue(name, Exchange(name, type="direct"), routing_key=name) for name in QUEUES
        ],
        task_routes=TASK_ROUTES,
        task_default_queue=MAINTENANCE_QUEUE,
        task_default_exchange=MAINTENANCE_QUEUE,
        task_default_routing_key=MAINTENANCE_QUEUE,
        timezone="UTC",
        enable_utc=True,
        beat_sync_every=1,
        beat_schedule={
            "heartbeat": {"task": HEARTBEAT_TASK, "schedule": HEARTBEAT_INTERVAL},
        },
    )
    return celery

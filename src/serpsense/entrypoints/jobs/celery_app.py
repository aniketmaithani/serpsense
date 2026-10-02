"""Celery application (ADR-0005). Postgres is the source of truth; messages are only nudges.

Run: `celery -A serpsense.entrypoints.jobs.celery_app worker -Q scans,outbox,maintenance`
"""

from celery import Celery
from kombu import Queue

from serpsense.composition import build_container
from serpsense.config import Settings

QUEUES = ("scans", "outbox", "maintenance")
SCAN_TIME_LIMIT_SECONDS = 15 * 60
# Must exceed the largest task time limit so unacked messages aren't redelivered mid-run.
VISIBILITY_TIMEOUT_SECONDS = 2 * SCAN_TIME_LIMIT_SECONDS


def create_celery(settings: Settings) -> Celery:
    celery = Celery("serpsense", broker=settings.redis_url.get_secret_value())
    celery.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_ignore_result=True,
        result_backend=None,
        task_time_limit=SCAN_TIME_LIMIT_SECONDS,
        task_soft_time_limit=SCAN_TIME_LIMIT_SECONDS - 60,
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT_SECONDS},
        broker_connection_retry_on_startup=True,
        task_queues=[Queue(name) for name in QUEUES],
        task_default_queue="maintenance",
        timezone="UTC",
        enable_utc=True,
        beat_schedule={},
    )
    return celery


app = create_celery(build_container().settings)

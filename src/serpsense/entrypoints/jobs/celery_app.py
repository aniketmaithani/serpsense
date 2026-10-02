"""Celery entrypoint for worker and beat.

Run: `celery -A serpsense.entrypoints.jobs.celery_app worker -Q scans,outbox,maintenance`
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from serpsense.composition import HEARTBEAT_TASK, build_celery, build_settings
from serpsense.observability import get_logger

log = get_logger(__name__)

# Only settings are loaded here (no DB engine or Redis client), so forked workers
# don't inherit connection pools from the prefork parent.
_settings = build_settings()
app: Celery = build_celery(_settings)


@setup_logging.connect
def _keep_our_logging(**_: Any) -> None:
    """Having a receiver stops Celery from installing its own (unscrubbed) handlers."""
    build_settings(_settings)


@app.task(name=HEARTBEAT_TASK)
def heartbeat() -> None:
    """Proves beat -> broker -> worker is flowing; beat's healthcheck watches its schedule file."""
    log.info("maintenance.heartbeat_received")

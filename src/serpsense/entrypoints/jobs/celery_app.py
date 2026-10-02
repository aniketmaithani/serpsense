"""Celery entrypoint for worker and beat.

Run: `celery -A serpsense.entrypoints.jobs.celery_app worker -Q scans,outbox,maintenance`
"""

from celery import Celery

from serpsense.composition import build_celery, build_settings
from serpsense.observability import get_logger

log = get_logger(__name__)

# Only settings are loaded here (no DB engine or Redis client), so forked workers
# don't inherit connection pools from the prefork parent.
app: Celery = build_celery(build_settings())


@app.task(name="serpsense.maintenance.heartbeat")
def heartbeat() -> None:
    """Proves beat → broker → worker is flowing; beat's healthcheck watches its schedule file."""
    log.info("maintenance.heartbeat_received")

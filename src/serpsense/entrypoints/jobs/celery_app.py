"""Celery entrypoint for worker and beat.

Run: `celery -A serpsense.entrypoints.jobs.celery_app worker -Q scans,outbox,maintenance`
"""

import uuid
from functools import cache
from typing import Any

from celery import Celery
from celery.signals import setup_logging
from structlog.contextvars import bound_contextvars

from serpsense.composition import (
    DISPATCH_TASK,
    HEARTBEAT_TASK,
    RUN_SCAN_TASK,
    SCAN_TIME_LIMIT_SECONDS,
    SWEEP_TASK,
    Worker,
    build_celery,
    build_settings,
    build_worker,
)
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


@cache
def _worker() -> Worker:
    """Built on a worker process's first task, after the fork, and never changed after."""
    return build_worker(_settings, app)


@app.task(name=RUN_SCAN_TASK, time_limit=SCAN_TIME_LIMIT_SECONDS)
def run_scan(scan_id: str) -> None:
    """A nudge: the scan service claims the scan, or finds it already taken. No soft time limit:
    its exception is an ordinary Exception that would read as a failed call or stage (#47);
    collection keeps to its own deadline, and the hard limit is the backstop."""
    with bound_contextvars(scan_id=scan_id):
        _worker().scans.run(uuid.UUID(scan_id))


@app.task(name=DISPATCH_TASK)
def dispatch_due_scans() -> None:
    _worker().dispatcher.dispatch()


@app.task(name=SWEEP_TASK)
def sweep_stuck_work() -> None:
    _worker().sweeper.sweep()

"""The composition root's operator-console half (ADR-0014): built only when `ADMIN_PASSWORD` is
set. Kept apart from `composition`, which wires it into the web container, so each stays within
its size budget."""

from celery import Celery
from sqlalchemy import Engine

from serpsense.adapters.cache.rate_limiter import RedisRateLimiter
from serpsense.adapters.crypto.keys import KeyPurpose, derive_key
from serpsense.adapters.db.console import SqlConsoleReads
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.adapters.jobs.celery_queue import CeleryJobQueue
from serpsense.adapters.system_clock import SystemClock
from serpsense.config import Settings
from serpsense.domain.console import console_keys
from serpsense.services.console import Console, ConsoleGate, GatePorts


def build_console(settings: Settings, engine: Engine, celery: Celery) -> Console | None:
    """The console, with its keys derived from SECRET_KEY and the password; None (the console
    answers 404) without a password. Nothing connects until a request comes."""
    if settings.admin_password is None:
        return None
    secret = settings.secret_key.get_secret_value()
    keys = console_keys(
        derive_key(secret, KeyPurpose.CONSOLE), settings.admin_password.get_secret_value()
    )
    limiter_key = derive_key(secret, KeyPurpose.RATE_LIMIT)
    ports = GatePorts(
        lambda: SqlUnitOfWork(engine, CeleryJobQueue(celery)),
        RedisRateLimiter(settings.redis_url.get_secret_value(), limiter_key),
        SystemClock(),
    )
    reads = SqlConsoleReads(engine.connect)
    gate = ConsoleGate(ports, keys)
    return Console(gate, reads, ports.unit_of_work, ports.clock, default_mode=settings.signup_mode)

"""Composition root: the only place that wires adapters into services (AGENTS.md §2)."""

from dataclasses import dataclass

from celery import Celery

from serpsense.adapters.cache.health import RedisHealthCheck
from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.health import PostgresHealthCheck
from serpsense.adapters.jobs.celery_factory import HEARTBEAT_TASK, create_celery
from serpsense.config import AppEnv, Settings
from serpsense.observability import configure_logging
from serpsense.ports.health import HealthCheck

__all__ = ["HEARTBEAT_TASK", "Container", "build_celery", "build_container", "build_settings"]


@dataclass(frozen=True)
class Container:
    settings: Settings
    health_checks: tuple[HealthCheck, ...]


def build_settings(settings: Settings | None = None) -> Settings:
    """Load settings and configure logging; no connections are created."""
    resolved = settings if settings is not None else Settings()
    configure_logging(
        level=resolved.log_level,
        json=resolved.app_env is not AppEnv.DEVELOPMENT,
        known_secrets=resolved.secret_values(),
    )
    return resolved


def build_container(settings: Settings | None = None) -> Container:
    resolved = build_settings(settings)
    engine = create_db_engine(resolved.database_url.get_secret_value())
    return Container(
        settings=resolved,
        health_checks=(
            PostgresHealthCheck(engine),
            RedisHealthCheck(resolved.redis_url.get_secret_value()),
        ),
    )


def build_celery(settings: Settings) -> Celery:
    return create_celery(settings.redis_url.get_secret_value())

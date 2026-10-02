"""Composition root: the only place that wires adapters into services (AGENTS.md §2)."""

from dataclasses import dataclass

from serpsense.adapters.cache.health import RedisHealthCheck
from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.health import PostgresHealthCheck
from serpsense.config import AppEnv, Settings
from serpsense.observability import configure_logging
from serpsense.ports.health import HealthCheck


@dataclass(frozen=True)
class Container:
    settings: Settings
    health_checks: tuple[HealthCheck, ...]


def build_container(settings: Settings | None = None) -> Container:
    resolved = settings if settings is not None else Settings()
    configure_logging(level=resolved.log_level, json=resolved.app_env is not AppEnv.DEVELOPMENT)
    engine = create_db_engine(resolved.database_url.get_secret_value())
    return Container(
        settings=resolved,
        health_checks=(
            PostgresHealthCheck(engine),
            RedisHealthCheck(resolved.redis_url.get_secret_value()),
        ),
    )

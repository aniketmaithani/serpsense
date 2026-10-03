"""Real Postgres and Redis via testcontainers (AGENTS.md §9: same engine as production)."""

import pytest

from serpsense.adapters.cache.health import RedisHealthCheck
from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.health import PostgresHealthCheck

pytestmark = pytest.mark.integration


def test_postgres_check_passes_when_database_reachable(postgres_url: str) -> None:
    engine = create_db_engine(postgres_url)
    try:
        assert PostgresHealthCheck(engine).check() is True
    finally:
        engine.dispose()


def test_postgres_check_fails_without_raising_when_unreachable() -> None:
    engine = create_db_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")
    try:
        assert PostgresHealthCheck(engine).check() is False
    finally:
        engine.dispose()


def test_redis_check_passes_when_reachable(redis_url: str) -> None:
    assert RedisHealthCheck(redis_url).check() is True


def test_redis_check_fails_without_raising_when_unreachable() -> None:
    assert RedisHealthCheck("redis://127.0.0.1:1/0").check() is False

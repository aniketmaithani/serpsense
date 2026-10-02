"""Shared Postgres for integration tests, migrated with the real Alembic migrations."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine
from testcontainers.postgres import PostgresContainer

from serpsense.adapters.db.engine import create_db_engine

ROOT = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str) -> Iterator[Engine]:
    """Proves the migrations are reversible and drift-free, then leaves the schema at head."""
    config = alembic_config()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", postgres_url)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)
    engine = create_db_engine(postgres_url)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(migrated_engine: Engine) -> Iterator[Connection]:
    """A connection whose work is always rolled back, so tests never see each other's rows."""
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()

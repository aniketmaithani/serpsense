"""Shared Postgres for integration tests, migrated with the real Alembic migrations."""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from serpsense.adapters.db.engine import create_db_engine

ROOT = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


THROWAWAY = "serpsense_test_"  # the only databases TEST_DATABASE_URL may name


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A throwaway Postgres: TEST_DATABASE_URL's when set (`scripts/dev.sh test` makes one in the
    native Postgres and drops it after), else a container. The migrations are run down to
    nothing on it, so a database that isn't a throwaway one is refused."""
    given = os.environ.get("TEST_DATABASE_URL")
    if given is None:
        with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
            yield container.get_connection_url()
        return
    if not (make_url(given).database or "").startswith(THROWAWAY):
        raise pytest.UsageError(f"TEST_DATABASE_URL must name a {THROWAWAY}* database")
    yield given


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """A throwaway Redis: TEST_REDIS_URL's when set (`scripts/dev.sh test` starts one and shuts
    it down after), else a container."""
    given = os.environ.get("TEST_REDIS_URL")
    if given is not None:
        yield given
        return
    with RedisContainer("redis:7-alpine") as container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


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


@pytest.fixture(scope="session")
def committing_engine(postgres_url: str) -> Iterator[Engine]:
    """A second migrated database for tests that must commit (units of work, two connections).
    Committed rows outlive the test, so these tests use brands of their own and never count rows
    they didn't make; the rolled-back `conn` tests never see them."""
    engine = create_db_engine(_migrated_database(postgres_url, "committed"))
    yield engine
    engine.dispose()


@pytest.fixture
def fresh_engine(postgres_url: str) -> Iterator[Engine]:
    """A migrated database of the test's own, for state every unit of work reads (the sign-up
    mode, ADR-0015): switches are append-only, so on a shared database one test's switch would
    hold for every test after it."""
    suffix = f"fresh_{uuid.uuid4().hex[:8]}"
    engine = create_db_engine(_migrated_database(postgres_url, suffix))
    yield engine
    engine.dispose()
    _drop_database(postgres_url, suffix)


def _migrated_database(postgres_url: str, suffix: str) -> str:
    """A new database next to the throwaway one, migrated to head; its URL."""
    name = f"{make_url(postgres_url).database}_{suffix}"
    admin = create_db_engine(postgres_url).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", url)
            command.upgrade(alembic_config(), "head")
    except Exception:
        _drop_database(postgres_url, suffix)  # a failed migration leaves no database behind
        raise
    return url


def _drop_database(postgres_url: str, suffix: str) -> None:
    """FORCE, so a connection a failing test left open can't keep it alive."""
    name = f"{make_url(postgres_url).database}_{suffix}"
    admin = create_db_engine(postgres_url).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def impatient_engine(committing_engine: Engine) -> Iterator[Engine]:
    """The committing database with a 200 ms lock timeout, for tests of a lock wait running out."""
    engine = create_engine(committing_engine.url, connect_args={"options": "-c lock_timeout=200"})
    yield engine
    engine.dispose()

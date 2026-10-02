"""Alembic environment: online migrations against DATABASE_URL."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from serpsense.adapters.db.base import Base

target_metadata = Base.metadata


def _database_url() -> str:
    # Migrations need only the database URL, not the full application settings.
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set to run migrations")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; run against a database.")
run_migrations_online()

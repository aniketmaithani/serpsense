"""Alembic environment: online migrations against DATABASE_URL."""

from alembic import context
from sqlalchemy import create_engine, pool

from serpsense.adapters.db import models  # noqa: F401  (registers all tables on Base.metadata)
from serpsense.adapters.db.base import Base
from serpsense.config import MigrationSettings

target_metadata = Base.metadata


def run_migrations_online() -> None:
    # Migrations need only the database URL, not the full application settings.
    url = MigrationSettings().database_url.get_secret_value()
    engine = create_engine(url, poolclass=pool.NullPool)
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

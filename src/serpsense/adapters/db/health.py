"""Postgres reachability check."""

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from serpsense.observability import get_logger

log = get_logger(__name__)


class PostgresHealthCheck:
    name = "postgres"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def check(self) -> bool:
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            log.warning("health.check_failed", dependency=self.name, error_type=type(exc).__name__)
            return False
        return True

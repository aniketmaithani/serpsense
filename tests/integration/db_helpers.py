"""Row builders and error inspection shared by the schema integration tests."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Table, insert
from sqlalchemy.exc import IntegrityError

from serpsense.adapters.db import models  # noqa: F401  (registers tables)
from serpsense.adapters.db.base import Base

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RESTRICT_VIOLATION = "23001"


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def add(conn: Connection, target: Table, **values: Any) -> uuid.UUID:
    row_id = uuid.uuid4()
    conn.execute(insert(target).values(id=row_id, **values))
    return row_id


def add_user(conn: Connection, email: str = "owner@example.com") -> uuid.UUID:
    return add(conn, table("users"), email=email, created_at=NOW)


def violation(exc: pytest.ExceptionInfo[IntegrityError]) -> Any:
    """psycopg's diagnostics for the error Postgres raised."""
    return exc.value.orig.diag  # type: ignore[union-attr]  # orig is the DBAPI error

"""Row builders and error inspection shared by the schema integration tests."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Table, insert
from sqlalchemy.exc import DBAPIError

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


def add_brand(
    conn: Connection, owner_id: uuid.UUID, slug: str = "voltbox", **overrides: Any
) -> uuid.UUID:
    values: dict[str, Any] = {"owner_id": owner_id, "name": "VoltBox", "slug": slug}
    return add(conn, table("brands"), created_at=NOW, **{**values, **overrides})


def add_owned_brand(conn: Connection, slug: str = "voltbox") -> uuid.UUID:
    """A brand with its own owner, so brands built in one test never share an owner."""
    return add_brand(conn, add_user(conn, f"{slug}@example.com"), slug=slug)


def add_scan(conn: Connection, brand_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "trigger": "schedule",
        "scheduled_for": NOW,
        "status": "queued",
        "settings_snapshot": {"preset": "standard"},
        "estimated_searches": 14,
        "created_at": NOW,
    }
    return add(conn, table("scans"), brand_id=brand_id, **{**values, **overrides})


def add_mention(conn: Connection, brand_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "source": "news",
        "identity_key": hashlib.sha256(b"https://news.example.in/voltbox-battery").hexdigest(),
        "text": "VoltBox earbuds recalled after battery complaints",
        "url": "https://news.example.in/voltbox-battery",
        "outlet": "Example News",
        "language_code": "en",
        "published_at": NOW,
        "created_at": NOW,
    }
    return add(conn, table("mentions"), brand_id=brand_id, **{**values, **overrides})


def add_location(conn: Connection, brand_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values = {"query": "VoltBox Indiranagar", **overrides}
    return add(conn, table("brand_locations"), brand_id=brand_id, **values)


def add_app(conn: Connection, brand_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values = {"store": "google_play", "app_id": "com.voltbox.connect", **overrides}
    return add(conn, table("brand_apps"), brand_id=brand_id, **values)


def violation(exc: pytest.ExceptionInfo[DBAPIError]) -> Any:
    """psycopg's diagnostics for the error Postgres raised."""
    return exc.value.orig.diag  # type: ignore[union-attr]  # orig is the DBAPI error

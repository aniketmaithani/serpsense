"""Versioned settings and schedule rules enforced by Postgres itself (data-model.md §2)."""

import uuid
from datetime import time, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_brand,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

DOCUMENT_TABLES = {
    "user_llm_profile_versions": "user_id",
    "user_search_default_versions": "user_id",
    "brand_search_settings_versions": "brand_id",
}
APPEND_ONLY = [*DOCUMENT_TABLES, "brand_schedule_versions"]
SCHEDULES = table("brand_schedule_versions")


def owner_of(conn: Connection, key: str) -> uuid.UUID:
    """A fresh user or brand id for the given key column."""
    user_id = add_user(conn)
    return user_id if key == "user_id" else add_brand(conn, user_id)


def add_document(conn: Connection, name: str, key_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values = {"document": {"preset": "balanced"}, "schema_version": 1, "created_at": NOW}
    return add(conn, table(name), **{DOCUMENT_TABLES[name]: key_id, **values, **overrides})


def add_schedule(conn: Connection, brand_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {"interval_minutes": 360, "created_at": NOW}
    return add(conn, SCHEDULES, brand_id=brand_id, **{**values, **overrides})


@pytest.mark.parametrize("name", DOCUMENT_TABLES)
def test_document_round_trips(conn: Connection, name: str) -> None:
    document = {"preset": "high_thinking", "tasks": {"draft_response": {"effort": "xhigh"}}}
    row_id = add_document(conn, name, owner_of(conn, DOCUMENT_TABLES[name]), document=document)
    stored = conn.execute(select(table(name).c.document).where(table(name).c.id == row_id))
    assert stored.scalar_one() == document


@pytest.mark.parametrize("name", DOCUMENT_TABLES)
@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"document": ["not", "an", "object"]}, "document_is_object"),
        ({"document": "balanced"}, "document_is_object"),
        ({"schema_version": 0}, "schema_version_positive"),
    ],
)
def test_document_checks(
    conn: Connection, name: str, overrides: dict[str, Any], check: str
) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_document(conn, name, owner_of(conn, DOCUMENT_TABLES[name]), **overrides)
    assert violation(exc).constraint_name == f"ck_{name}_{check}"


@pytest.mark.parametrize("name", DOCUMENT_TABLES)
def test_one_document_version_per_key_per_instant(conn: Connection, name: str) -> None:
    key = DOCUMENT_TABLES[name]
    key_id = owner_of(conn, key)
    add_document(conn, name, key_id)
    add_document(conn, name, key_id, created_at=NOW + timedelta(seconds=1))
    with pytest.raises(IntegrityError) as exc:
        add_document(conn, name, key_id)
    assert violation(exc).constraint_name == f"uq_{name}_{key}_created_at"


@pytest.mark.parametrize("interval", [60, 180, 360, 720, 1440, None])
def test_allowed_intervals_are_accepted(conn: Connection, interval: int | None) -> None:
    add_schedule(conn, owner_of(conn, "brand_id"), interval_minutes=interval)


def test_schedule_defaults_timezone_and_round_trips(conn: Connection) -> None:
    schedule_id = add_schedule(
        conn, owner_of(conn, "brand_id"), quiet_start=time(23, 0), quiet_end=time(6, 0)
    )
    row = conn.execute(select(SCHEDULES).where(SCHEDULES.c.id == schedule_id)).one()
    assert (row.timezone, row.quiet_start, row.quiet_end) == ("Asia/Kolkata", time(23), time(6))


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"interval_minutes": 30}, "interval_allowed"),
        ({"quiet_start": time(23, 0)}, "quiet_hours_valid"),
        ({"quiet_end": time(6, 0)}, "quiet_hours_valid"),
        ({"quiet_start": time(1, 0), "quiet_end": time(1, 0)}, "quiet_hours_valid"),
        ({"timezone": ""}, "timezone_format"),
        ({"timezone": "Asia/ Kolkata"}, "timezone_format"),
    ],
)
def test_schedule_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_schedule(conn, owner_of(conn, "brand_id"), **overrides)
    assert violation(exc).constraint_name == f"ck_brand_schedule_versions_{check}"


def test_one_schedule_version_per_brand_per_instant(conn: Connection) -> None:
    brand_id = owner_of(conn, "brand_id")
    add_schedule(conn, brand_id)
    with pytest.raises(IntegrityError) as exc:
        add_schedule(conn, brand_id, interval_minutes=60)
    assert violation(exc).constraint_name == "uq_brand_schedule_versions_brand_id_created_at"


def _first_row(conn: Connection, name: str) -> uuid.UUID:
    if name == "brand_schedule_versions":
        return add_schedule(conn, owner_of(conn, "brand_id"))
    return add_document(conn, name, owner_of(conn, DOCUMENT_TABLES[name]))


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
@pytest.mark.parametrize("name", APPEND_ONLY)
def test_versions_are_append_only(conn: Connection, name: str, mutation: str) -> None:
    versions = table(name)
    row_id = _first_row(conn, name)
    statements: dict[str, Executable] = {
        "update": update(versions).where(versions.c.id == row_id).values(created_at=NOW),
        "delete": delete(versions).where(versions.c.id == row_id),
        "truncate": text(f"TRUNCATE {name}"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


@pytest.mark.parametrize("name", APPEND_ONLY)
def test_referenced_owner_cannot_be_deleted(conn: Connection, name: str) -> None:
    row_id = _first_row(conn, name)
    key = DOCUMENT_TABLES.get(name, "brand_id")
    key_id = conn.execute(select(table(name).c[key]).where(table(name).c.id == row_id)).scalar_one()
    target = "users" if key == "user_id" else "brands"
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table(target)).where(table(target).c.id == key_id))
    assert violation(exc).constraint_name == f"fk_{name}_{key}_{target}"

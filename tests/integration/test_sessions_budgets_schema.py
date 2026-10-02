"""Sessions and budget rules enforced by Postgres itself (data-model.md §1)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

USERS = table("users")
SESSIONS = table("sessions")
BUDGETS = {
    "user_search_budgets": {"monthly_searches": 1500},
    "user_llm_budgets": {"monthly_micros": 30_000_000, "currency": "USD"},
}


def add_session(conn: Connection, user_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "user_id": user_id,
        "token_hash": uuid.uuid4().bytes,
        "csrf_secret": b"\x02" * 32,
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=7),
    }
    return add(conn, SESSIONS, **{**values, **overrides})


def add_budget(conn: Connection, name: str, user_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values = {**BUDGETS[name], "effective_from": NOW, "created_at": NOW, **overrides}
    return add(conn, table(name), user_id=user_id, **values)


def test_session_round_trips_with_max_length_user_agent(conn: Connection) -> None:
    session_id = add_session(conn, add_user(conn), user_agent="x" * 256, ip="203.0.113.7")
    row = conn.execute(select(SESSIONS).where(SESSIONS.c.id == session_id)).one()
    assert row.user_agent == "x" * 256
    assert str(row.ip) == "203.0.113.7"
    assert row.revoked_at is None


def test_session_token_hash_is_unique(conn: Connection) -> None:
    user_id = add_user(conn)
    add_session(conn, user_id, token_hash=b"\x03" * 32)
    with pytest.raises(IntegrityError) as exc:
        add_session(conn, user_id, token_hash=b"\x03" * 32)
    assert violation(exc).constraint_name == "uq_sessions_token_hash"


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"user_agent": "x" * 257}, "ck_sessions_user_agent_length"),
        ({"expires_at": NOW}, "ck_sessions_expires_after_created"),
    ],
)
def test_session_checks(conn: Connection, overrides: dict[str, Any], constraint: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_session(conn, add_user(conn), **overrides)
    assert violation(exc).constraint_name == constraint


@pytest.mark.parametrize(
    ("child", "constraint"),
    [
        ("sessions", "fk_sessions_user_id_users"),
        ("user_search_budgets", "fk_user_search_budgets_user_id_users"),
        ("user_llm_budgets", "fk_user_llm_budgets_user_id_users"),
    ],
)
def test_user_with_dependents_cannot_be_deleted(
    conn: Connection, child: str, constraint: str
) -> None:
    user_id = add_user(conn)
    if child == "sessions":
        add_session(conn, user_id)
    else:
        add_budget(conn, child, user_id)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(USERS).where(USERS.c.id == user_id))
    assert violation(exc).constraint_name == constraint


@pytest.mark.parametrize("name", list(BUDGETS))
def test_budget_round_trips(conn: Connection, name: str) -> None:
    budget_id = add_budget(conn, name, add_user(conn))
    row = conn.execute(select(table(name)).where(table(name).c.id == budget_id)).one()
    for column, value in BUDGETS[name].items():
        assert getattr(row, column) == value


@pytest.mark.parametrize(
    ("name", "overrides", "constraint"),
    [
        (
            "user_search_budgets",
            {"monthly_searches": -1},
            "ck_user_search_budgets_monthly_searches_non_negative",
        ),
        (
            "user_llm_budgets",
            {"monthly_micros": -1},
            "ck_user_llm_budgets_monthly_micros_non_negative",
        ),
        ("user_llm_budgets", {"currency": "usd"}, "ck_user_llm_budgets_currency_iso_4217"),
    ],
)
def test_budget_checks(
    conn: Connection, name: str, overrides: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_budget(conn, name, add_user(conn), **overrides)
    assert violation(exc).constraint_name == constraint


@pytest.mark.parametrize("name", list(BUDGETS))
def test_one_budget_row_per_user_per_instant(conn: Connection, name: str) -> None:
    user_id = add_user(conn)
    add_budget(conn, name, user_id)
    add_budget(conn, name, user_id, effective_from=NOW + timedelta(days=1))
    with pytest.raises(IntegrityError) as exc:
        add_budget(conn, name, user_id)
    assert violation(exc).constraint_name == f"uq_{name}_user_id_effective_from"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
@pytest.mark.parametrize("name", list(BUDGETS))
def test_budgets_are_append_only(conn: Connection, name: str, mutation: str) -> None:
    budgets = table(name)
    budget_id = add_budget(conn, name, add_user(conn))
    statements: dict[str, Executable] = {
        "update": update(budgets).where(budgets.c.id == budget_id).values(created_at=NOW),
        "delete": delete(budgets).where(budgets.c.id == budget_id),
        "truncate": text(f"TRUNCATE {name}"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    diag = violation(exc)
    assert diag.sqlstate == RESTRICT_VIOLATION
    assert diag.message_primary == f"table {name} is append-only ({mutation.upper()} rejected)"

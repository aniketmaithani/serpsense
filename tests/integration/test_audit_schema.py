"""Audit log rules enforced by Postgres itself (data-model.md §9)."""

from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import NOW, RESTRICT_VIOLATION, add, add_user, table, violation

pytestmark = pytest.mark.integration

EVENTS, NETWORK = table("audit_events"), table("audit_event_network")


def event(conn: Connection, **values: Any) -> Any:
    return add(conn, EVENTS, **{"action": "auth.logged_out", "created_at": NOW, **values})


@pytest.mark.parametrize(
    ("values", "check"),
    [
        ({"action": "logged out"}, "action_format"),
        ({"action": "Auth.LoggedOut"}, "action_format"),
        ({"target_type": "otp_code"}, "target_type_iff_target_id"),
    ],
)
def test_event_checks(conn: Connection, values: dict[str, Any], check: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        event(conn, **values)
    assert violation(exc).constraint_name == f"ck_audit_events_{check}"


def test_network_details_can_be_scrubbed(conn: Connection) -> None:
    event_id = event(conn, actor_user_id=add_user(conn))
    row = {"audit_event_id": event_id, "ip": "203.0.113.7", "user_agent": "UA"}
    conn.execute(NETWORK.insert().values(row))
    conn.execute(update(NETWORK).values(ip=None, user_agent=None))  # account deletion's scrub
    assert tuple(conn.execute(select(NETWORK.c.ip, NETWORK.c.user_agent)).one()) == (None, None)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(update(NETWORK).values(user_agent="x" * 257))
    assert violation(exc).constraint_name == "ck_audit_event_network_user_agent_length"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_events_are_append_only(conn: Connection, mutation: str) -> None:
    event(conn)
    statements: dict[str, Executable] = {
        "update": update(EVENTS).values(created_at=func.now()),
        "delete": delete(EVENTS),
        "truncate": text("TRUNCATE audit_events CASCADE"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION

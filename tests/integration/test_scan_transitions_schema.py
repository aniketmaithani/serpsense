"""Scan status transition rules enforced by Postgres itself (data-model.md §4)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_brand,
    add_scan,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

TRANSITIONS = table("scan_status_transitions")
ALLOWED = [
    (None, "queued"),
    ("queued", "running"),
    ("queued", "skipped"),
    ("running", "succeeded"),
    ("running", "partial"),
    ("running", "failed"),
    ("running", "skipped"),
]
STATUSES = ["queued", "running", "succeeded", "partial", "failed", "skipped"]
# Every other (from, to) pair, including from NULL: 7 x 6 - 7 allowed = 35 forbidden.
FORBIDDEN = [
    (before, after)
    for before in [None, *STATUSES]
    for after in STATUSES
    if (before, after) not in ALLOWED
]


def scan(conn: Connection) -> uuid.UUID:
    return add_scan(conn, add_brand(conn, add_user(conn)))


def add_transition(conn: Connection, scan_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "from_status": None,
        "to_status": "queued",
        "actor": "system",
        "reason": "scheduled",
        "at": NOW,
    }
    return add(conn, TRANSITIONS, scan_id=scan_id, **{**values, **overrides})


@pytest.mark.parametrize(("from_status", "to_status"), ALLOWED)
def test_allowed_transitions_are_accepted(
    conn: Connection, from_status: str | None, to_status: str
) -> None:
    add_transition(conn, scan(conn), from_status=from_status, to_status=to_status)


@pytest.mark.parametrize(("from_status", "to_status"), FORBIDDEN)
def test_forbidden_transitions_are_rejected(
    conn: Connection, from_status: str | None, to_status: str
) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_transition(conn, scan(conn), from_status=from_status, to_status=to_status)
    assert violation(exc).constraint_name == "ck_scan_status_transitions_allowed_transition"


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"actor": "user"}, "actor_user_matches"),
        ({"actor_user_id": "owner"}, "actor_user_matches"),
        ({"reason": "Timed out"}, "reason_format"),
        ({"reason": ""}, "reason_format"),
    ],
)
def test_transition_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    owner = add_user(conn)
    if overrides.get("actor_user_id") == "owner":
        overrides = {**overrides, "actor_user_id": owner}
    with pytest.raises(IntegrityError) as exc:
        add_transition(conn, add_scan(conn, add_brand(conn, owner)), **overrides)
    assert violation(exc).constraint_name == f"ck_scan_status_transitions_{check}"


def test_forbidden_list_covers_every_other_pair() -> None:
    assert len(FORBIDDEN) == 35


def test_a_scan_reaches_each_status_once(conn: Connection) -> None:
    scan_id = scan(conn)
    add_transition(conn, scan_id)
    with pytest.raises(IntegrityError) as exc:
        add_transition(conn, scan_id)
    assert violation(exc).constraint_name == "uq_scan_status_transitions_scan_id_to_status"


def test_user_transition_round_trips(conn: Connection) -> None:
    owner = add_user(conn)
    scan_id = add_scan(conn, add_brand(conn, owner))
    row_id = add_transition(
        conn,
        scan_id,
        from_status="queued",
        to_status="skipped",
        actor="user",
        actor_user_id=owner,
        reason="brand_archived",
    )
    row = conn.execute(select(TRANSITIONS).where(TRANSITIONS.c.id == row_id)).one()
    assert (row.from_status, row.to_status, row.actor_user_id) == ("queued", "skipped", owner)


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_transitions_are_append_only(conn: Connection, mutation: str) -> None:
    name = TRANSITIONS.name
    scan_id = scan(conn)
    add_transition(conn, scan_id)
    history = table(name)
    statements: dict[str, Executable] = {
        "update": update(history).where(history.c.scan_id == scan_id).values(scan_id=scan_id),
        "delete": delete(history).where(history.c.scan_id == scan_id),
        "truncate": text(f"TRUNCATE {name}"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


def test_scan_with_transitions_cannot_be_deleted(conn: Connection) -> None:
    name = TRANSITIONS.name
    scan_id = scan(conn)
    add_transition(conn, scan_id)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("scans")).where(table("scans").c.id == scan_id))
    assert violation(exc).constraint_name == f"fk_{name}_scan_id_scans"

"""Outbox rules enforced by Postgres itself (data-model.md §8, ADR-0010)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, func, text, update
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

MESSAGES, ATTEMPTS = table("outbox_messages"), table("outbox_attempts")


def alert(conn: Connection) -> dict[str, uuid.UUID]:
    """An alert, and its brand's owner: what an alert email names."""
    owner = add_user(conn, f"{uuid.uuid4().hex[:8]}@example.com")
    scan_id = add_scan(conn, add_brand(conn, owner), status="succeeded")
    alert_id = add(conn, table("alerts"), scan_id=scan_id, rule="level_increase", created_at=NOW)
    return {"alert_id": alert_id, "user_id": owner}


def message(conn: Connection, **values: Any) -> uuid.UUID:
    row: dict[str, Any] = {
        "kind": "alert_email",
        "recipient_email": "owner@example.com",
        "template": "alert",
        "template_data": {"brand": "Ola"},
        "dedupe_key": f"alert:{uuid.uuid4()}:email",
        "status": "pending",
        "next_attempt_at": NOW,
        "created_at": NOW,
    }
    if "alert_id" not in values and values.get("kind", "alert_email") == "alert_email":
        row |= alert(conn)
    return add(conn, MESSAGES, **{**row, **values})


def attempt(conn: Connection, message_id: uuid.UUID, **values: Any) -> uuid.UUID:
    row = {"outbox_message_id": message_id, "attempted_at": NOW, "outcome": "sent", **values}
    return add(conn, ATTEMPTS, **row)


def test_a_message_is_written_once_per_dedupe_key(conn: Connection) -> None:
    refs = alert(conn)
    message(conn, dedupe_key="alert:x:email", **refs)
    with pytest.raises(IntegrityError) as exc:
        message(conn, dedupe_key="alert:x:email", **refs)
    assert violation(exc).constraint_name == "uq_outbox_messages_dedupe_key"


@pytest.mark.parametrize(
    ("values", "check"),
    [
        ({"alert_id": None}, "kind_refs"),  # an alert email names its alert
        ({"kind": "otp_email"}, "kind_refs"),  # an OTP email names its code, not an alert
        ({"status": "sent", "sensitive_data_encrypted": b"x"}, "sensitive_only_pending"),
        ({"dedupe_key": " "}, "dedupe_key_length"),
        ({"recipient_email": "a@x.in, b@y.in"}, "recipient_plain"),
        ({"recipient_email": "Owner <a@x.in>"}, "recipient_plain"),
        ({"user_id": None}, "alert_email_has_user"),
    ],
)
def test_message_checks(conn: Connection, values: dict[str, Any], check: str) -> None:
    if values.get("kind") == "otp_email":
        values = {**values, **alert(conn)}
    elif "alert_id" in values:  # an alert email without its alert, but with its user
        values = {**values, "user_id": alert(conn)["user_id"]}
    with pytest.raises(IntegrityError) as exc:
        message(conn, **values)
    assert violation(exc).constraint_name == f"ck_outbox_messages_{check}"


@pytest.mark.parametrize(
    ("values", "check"),
    [
        ({"outcome": "retryable_error"}, "error_code_iff_error"),
        ({"outcome": "sent", "error_code": "smtp.timeout"}, "error_code_iff_error"),
        ({"outcome": "permanent_error", "error_code": "SMTP 550"}, "error_code_format"),
    ],
)
def test_attempt_checks(conn: Connection, values: dict[str, Any], check: str) -> None:
    message_id = message(conn)
    with pytest.raises(IntegrityError) as exc:
        attempt(conn, message_id, **values)
    assert violation(exc).constraint_name == f"ck_outbox_attempts_{check}"


def test_a_messages_status_and_payload_can_change(conn: Connection) -> None:
    message_id = message(conn, sensitive_data_encrypted=b"ciphertext")
    attempt(conn, message_id, outcome="retryable_error", error_code="smtp.timeout")
    scrub = update(MESSAGES).values(status="sent", sensitive_data_encrypted=None)
    conn.execute(scrub.where(MESSAGES.c.id == message_id))  # status and payload change


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_attempts_are_append_only(conn: Connection, mutation: str) -> None:
    attempt(conn, message(conn))
    statements: dict[str, Executable] = {
        "update": update(ATTEMPTS).values(attempted_at=func.now()),
        "delete": delete(ATTEMPTS),
        "truncate": text("TRUNCATE outbox_attempts CASCADE"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION

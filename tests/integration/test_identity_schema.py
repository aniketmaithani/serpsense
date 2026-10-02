"""Identity schema rules enforced by Postgres itself: users and OTP (data-model.md §1)."""

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

OTP_CODES = table("otp_codes")
ATTEMPTS = table("otp_verify_attempts")


def add_otp(conn: Connection, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "email": "owner@example.com",
        "code_hash": b"\x01" * 32,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
    }
    return add(conn, OTP_CODES, **{**values, **overrides})


def add_attempt(conn: Connection, otp_code_id: uuid.UUID, *, succeeded: bool) -> uuid.UUID:
    return add(conn, ATTEMPTS, otp_code_id=otp_code_id, attempted_at=NOW, succeeded=succeeded)


def test_user_email_is_unique_case_insensitively(conn: Connection) -> None:
    add_user(conn, "Owner@Example.com")
    with pytest.raises(IntegrityError) as exc:
        add_user(conn, "owner@EXAMPLE.com")
    assert violation(exc).constraint_name == "uq_users_email"


def test_otp_cannot_be_both_consumed_and_superseded(conn: Connection) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_otp(conn, consumed_at=NOW, superseded_at=NOW)
    assert violation(exc).constraint_name == "ck_otp_codes_single_terminal"


def test_otp_must_expire_after_creation(conn: Connection) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_otp(conn, expires_at=NOW)
    assert violation(exc).constraint_name == "ck_otp_codes_expires_after_created"


def test_only_one_live_otp_per_email(conn: Connection) -> None:
    add_otp(conn, email="owner@example.com")
    with pytest.raises(IntegrityError) as exc:
        add_otp(conn, email="OWNER@example.com")
    assert violation(exc).constraint_name == "uq_otp_codes_one_live_per_email"


def test_new_otp_allowed_once_previous_is_superseded(conn: Connection) -> None:
    first = add_otp(conn)
    conn.execute(update(OTP_CODES).where(OTP_CODES.c.id == first).values(superseded_at=NOW))
    second = add_otp(conn)
    live = conn.execute(
        select(OTP_CODES.c.id).where(
            OTP_CODES.c.consumed_at.is_(None), OTP_CODES.c.superseded_at.is_(None)
        )
    ).scalars()
    assert list(live) == [second]


def test_code_verifies_successfully_at_most_once(conn: Connection) -> None:
    otp_id = add_otp(conn)
    add_attempt(conn, otp_id, succeeded=False)
    add_attempt(conn, otp_id, succeeded=True)
    with pytest.raises(IntegrityError) as exc:
        add_attempt(conn, otp_id, succeeded=True)
    assert violation(exc).constraint_name == "uq_otp_verify_attempts_one_success_per_code"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_verify_attempts_are_append_only(conn: Connection, mutation: str) -> None:
    attempt_id = add_attempt(conn, add_otp(conn), succeeded=False)
    statements: dict[str, Executable] = {
        "update": update(ATTEMPTS).where(ATTEMPTS.c.id == attempt_id).values(succeeded=True),
        "delete": delete(ATTEMPTS).where(ATTEMPTS.c.id == attempt_id),
        "truncate": text("TRUNCATE otp_verify_attempts"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    diag = violation(exc)
    assert diag.sqlstate == RESTRICT_VIOLATION
    assert diag.message_primary == (
        f"table otp_verify_attempts is append-only ({mutation.upper()} rejected)"
    )


def test_otp_code_with_attempts_cannot_be_deleted(conn: Connection) -> None:
    otp_id = add_otp(conn)
    add_attempt(conn, otp_id, succeeded=False)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(OTP_CODES).where(OTP_CODES.c.id == otp_id))
    assert violation(exc).constraint_name == "fk_otp_verify_attempts_otp_code_id_otp_codes"

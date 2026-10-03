"""The sign-in code store on real Postgres (ADR-0009): one live code per email, requests and
guesses that queue instead of racing past the limits."""

import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

from serpsense.adapters.db.otp_codes import SqlOtpCodes
from tests.integration.db_helpers import NOW

pytestmark = pytest.mark.integration

MINUTE = timedelta(minutes=1)
EMAIL = "owner@example.com"
WAITING = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)


def issue(conn: Connection, at_minute: int = 0, email: str = EMAIL) -> uuid.UUID | None:
    at = NOW + at_minute * MINUTE
    return SqlOtpCodes(conn).issue(email, b"hash", at=at, expires_at=at + 10 * MINUTE, ip=None)


def fresh_email() -> str:
    return f"{uuid.uuid4().hex[:10]}@example.com"


def in_parallel(
    engine: Engine, work: Callable[[Connection], Any]
) -> tuple[threading.Thread, dict[str, Any]]:
    """Run `work` in its own transaction on another thread, and return once it is waiting for a
    lock (polled, no sleeping)."""
    result: dict[str, Any] = {}

    def run() -> None:
        with engine.begin() as conn:
            result["value"] = work(conn)

    thread = threading.Thread(target=run)
    thread.start()
    with engine.connect() as watcher:
        for _ in range(100_000):
            if watcher.execute(WAITING).scalar_one() > 0:
                return thread, result
    raise AssertionError("the parallel transaction never waited")


def test_a_new_code_supersedes_the_live_one(conn: Connection) -> None:
    codes = SqlOtpCodes(conn)
    first, second = issue(conn), issue(conn, 2)
    assert codes.issued_since(EMAIL.upper(), NOW) == [NOW, NOW + 2 * MINUTE]  # case ignored
    live = codes.lock_live(EMAIL)
    assert live is not None and live.code_id == second != first
    assert second is not None
    codes.attempt(second, succeeded=False, at=NOW + 3 * MINUTE)
    codes.attempt(second, succeeded=False, at=NOW + 4 * MINUTE)
    assert (live := codes.lock_live(EMAIL)) is not None and live.failed_attempts == 2
    codes.attempt(second, succeeded=True, at=NOW + 5 * MINUTE)
    codes.consume(second, at=NOW + 5 * MINUTE)
    assert codes.lock_live(EMAIL) is None  # used once
    assert codes.issued_since(EMAIL, NOW + MINUTE) == [NOW + 2 * MINUTE]


def test_a_guess_that_waited_counts_the_wrong_guess_before_it(committing_engine: Engine) -> None:
    email = fresh_email()
    with committing_engine.begin() as conn:
        issue(conn, email=email)
    with committing_engine.begin() as first:
        live = SqlOtpCodes(first).lock_live(email)
        assert live is not None and live.failed_attempts == 0
        thread, waited = in_parallel(committing_engine, lambda c: SqlOtpCodes(c).lock_live(email))
        SqlOtpCodes(first).attempt(live.code_id, succeeded=False, at=NOW)
    thread.join()
    assert waited["value"].failed_attempts == 1  # not the count from before its wait


def test_requests_for_one_email_queue(committing_engine: Engine) -> None:
    email = fresh_email()
    with committing_engine.begin() as first, committing_engine.begin() as second:
        SqlOtpCodes(first).lock_requests(email)
        SqlOtpCodes(second).lock_requests(fresh_email())  # another email doesn't wait
        second.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            SqlOtpCodes(second).lock_requests(email.upper())


def test_a_request_that_loses_the_race_gets_no_code(committing_engine: Engine) -> None:
    email, other = fresh_email(), fresh_email()

    def race(conn: Connection) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        kept = issue(conn, email=other)  # work done before the clash survives it
        return kept, issue(conn, email=email)

    with committing_engine.begin() as first:
        assert issue(first, email=email) is not None
        thread, raced = in_parallel(committing_engine, race)
    thread.join()
    kept, lost = raced["value"]
    assert lost is None and kept is not None
    with committing_engine.begin() as conn:
        assert SqlOtpCodes(conn).lock_live(other) is not None


def test_any_other_integrity_error_is_raised(conn: Connection) -> None:
    with pytest.raises(IntegrityError, match="expires_after_created"):
        SqlOtpCodes(conn).issue(EMAIL, b"hash", at=NOW, expires_at=NOW, ip=None)


def test_codes_are_counted_for_every_address_together(conn: Connection) -> None:
    codes = SqlOtpCodes(conn)
    before = codes.issued_in_all_since(NOW)
    issue(conn), issue(conn, email="other@example.com"), issue(conn, -5)
    assert codes.issued_in_all_since(NOW) == before + 2  # not the one from before `since`

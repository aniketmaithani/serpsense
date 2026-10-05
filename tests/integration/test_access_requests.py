"""Access requests, decisions and sign-up mode switches on real Postgres (data-model §1,
ADR-0014, ADR-0015)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, func, select

from serpsense.adapters.db.access import REQUESTS, SqlAccessRequests
from serpsense.domain.enums import AccessDecision, SignupMode
from serpsense.ports.access import ModeSwitch
from tests.integration.db_helpers import NOW, table

pytestmark = pytest.mark.integration

APPROVED, REJECTED = AccessDecision.APPROVED, AccessDecision.REJECTED


def request_id(conn: Connection, email: str) -> uuid.UUID:
    return conn.execute(select(REQUESTS.c.id).where(REQUESTS.c.email == email)).scalar_one()


def test_asking_again_in_any_case_keeps_one_request(conn: Connection) -> None:
    access = SqlAccessRequests(conn)
    access.record("New@Example.com", at=NOW)
    access.record("new@example.com", at=NOW + timedelta(hours=1))
    rows = conn.execute(select(func.count()).select_from(REQUESTS)).scalar_one()
    first = conn.execute(select(REQUESTS.c.requested_at)).scalar_one()
    assert rows == 1 and first == NOW


def test_the_latest_decision_counts(conn: Connection) -> None:
    access = SqlAccessRequests(conn)
    access.record("new@example.com", at=NOW)
    row = request_id(conn, "new@example.com")
    assert not access.approved("new@example.com")  # pending
    assert access.decide(row, APPROVED, at=NOW + timedelta(minutes=1))
    assert access.approved("NEW@example.com")
    assert access.decide(row, REJECTED, at=NOW + timedelta(minutes=2))
    assert not access.approved("new@example.com")


def test_a_second_decision_at_the_same_instant_changes_nothing(conn: Connection) -> None:
    access = SqlAccessRequests(conn)
    access.record("twice@example.com", at=NOW)
    row = request_id(conn, "twice@example.com")
    assert access.decide(row, APPROVED, at=NOW)
    assert access.decide(row, REJECTED, at=NOW)  # a double click: the first one stands
    assert access.approved("twice@example.com")


def test_new_requests_are_counted_across_addresses(conn: Connection) -> None:
    access, hour = SqlAccessRequests(conn), timedelta(hours=1)
    for minutes, email in enumerate(["a@example.com", "b@example.com", "a@example.com"]):
        access.record(email, at=NOW + timedelta(minutes=minutes))
    assert access.recorded_between(NOW, NOW + hour) == 2  # a repeat isn't a new request
    assert access.recorded_between(NOW + timedelta(minutes=1), NOW + hour) == 1
    assert access.recorded_between(NOW - hour, NOW - timedelta(seconds=1)) == 0


def test_unknown_addresses_and_requests(conn: Connection) -> None:
    access = SqlAccessRequests(conn)
    assert not access.approved("nobody@example.com")
    assert not access.decide(uuid.uuid4(), APPROVED, at=NOW)


def test_the_latest_sign_up_mode_switch_counts(conn: Connection) -> None:
    access = SqlAccessRequests(conn)
    assert access.signup_mode() is None  # never switched: the environment's mode applies
    access.switch_signup_mode(SignupMode.OPEN, at=NOW)
    access.switch_signup_mode(SignupMode.INVITE, at=NOW)  # a double click: the first one stands
    access.switch_signup_mode(SignupMode.INVITE, at=NOW + timedelta(minutes=1))
    assert access.signup_mode() == ModeSwitch(SignupMode.INVITE, NOW + timedelta(minutes=1))


def test_admitting_approves_an_address_once(conn: Connection) -> None:
    access = SqlAccessRequests(conn)
    access.admit("Open@Example.com", at=NOW)
    access.admit("open@example.com", at=NOW + timedelta(minutes=1))  # already approved: no new row
    assert access.approved("open@example.com")
    decisions = table("access_decisions")
    assert conn.execute(select(func.count()).select_from(decisions)).scalar_one() == 1

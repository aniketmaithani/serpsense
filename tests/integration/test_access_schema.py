"""Access request and decision rules enforced by Postgres itself (data-model §1, ADR-0014)."""

import uuid

import pytest
from sqlalchemy import Connection, Executable, delete, insert, text, update
from sqlalchemy.exc import DataError, IntegrityError

from tests.integration.db_helpers import NOW, RESTRICT_VIOLATION, add, table, violation

pytestmark = pytest.mark.integration

REQUESTS, DECISIONS = table("access_requests"), table("access_decisions")


def request(conn: Connection, email: str = "new@example.com") -> uuid.UUID:
    return add(conn, REQUESTS, email=email, requested_at=NOW)


def decide(conn: Connection, request_id: uuid.UUID, decision: str = "approved") -> uuid.UUID:
    return add(conn, DECISIONS, access_request_id=request_id, decision=decision, decided_at=NOW)


def test_an_address_has_one_request_whatever_its_case(conn: Connection) -> None:
    request(conn, "New@Example.com")
    with pytest.raises(IntegrityError) as exc:
        request(conn, "new@example.COM")
    assert violation(exc).constraint_name == "uq_access_requests_email"


def test_a_request_can_be_pseudonymised(conn: Connection) -> None:
    row = request(conn)
    pseudonym = f"deleted+{row}@serpsense.invalid"
    conn.execute(update(REQUESTS).where(REQUESTS.c.id == row).values(email=pseudonym))


def test_a_decision_names_a_request(conn: Connection) -> None:
    with pytest.raises(IntegrityError) as exc:
        decide(conn, uuid.uuid4())
    assert violation(exc).constraint_name == "fk_access_decisions_access_request_id_access_requests"


def test_a_decided_request_is_never_deleted(conn: Connection) -> None:
    row = request(conn)
    decide(conn, row, "rejected")
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(REQUESTS).where(REQUESTS.c.id == row))
    assert violation(exc).constraint_name == "fk_access_decisions_access_request_id_access_requests"


def test_an_unknown_answer_is_refused(conn: Connection) -> None:
    row = request(conn)
    with pytest.raises(DataError) as exc:
        conn.execute(
            insert(DECISIONS).values(
                id=uuid.uuid4(), access_request_id=row, decision="maybe", decided_at=NOW
            )
        )
    assert "access_decision" in str(exc.value)


def test_a_request_has_one_decision_per_instant(conn: Connection) -> None:
    row = request(conn)
    decide(conn, row, "approved")
    with pytest.raises(IntegrityError) as exc:
        decide(conn, row, "rejected")  # the same instant: which would be the latest?
    assert violation(exc).constraint_name == "uq_access_decisions_access_request_id_decided_at"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_decisions_are_append_only(conn: Connection, mutation: str) -> None:
    decide(conn, request(conn))
    statements: dict[str, Executable] = {
        "update": update(DECISIONS).values(decision="rejected"),
        "delete": delete(DECISIONS),
        "truncate": text("TRUNCATE access_decisions"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION

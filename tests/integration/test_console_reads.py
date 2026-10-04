"""What the operator console reads, across every user (ADR-0014)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import Connection

from serpsense.adapters.db.access import SqlAccessRequests
from serpsense.adapters.db.console import SqlConsoleReads
from serpsense.domain.enums import AccessDecision
from serpsense.ports.accounts import pseudonym
from serpsense.ports.console import Totals
from tests.integration.db_helpers import NOW, add_brand, add_scan, add_user, table

pytestmark = pytest.mark.integration


def reads(conn: Connection) -> SqlConsoleReads:
    @contextmanager
    def same() -> Iterator[Connection]:
        yield conn  # the test's own transaction, rolled back afterwards

    return SqlConsoleReads(same)


def test_the_totals_count_everyone(conn: Connection) -> None:
    users, brands = table("users"), table("brands")
    one, two = add_user(conn, "one@example.com"), add_user(conn, "two@example.com")
    gone = add_user(conn, "gone@example.com")
    conn.execute(users.update().where(users.c.id == gone).values(deleted_at=NOW))
    old = add_brand(conn, one, slug="old")
    month_ago = NOW - timedelta(days=30)
    conn.execute(brands.update().where(brands.c.id == old).values(created_at=month_ago))
    add_brand(conn, two, slug="new")
    add_brand(conn, two, slug="shelved", archived_at=NOW)
    add_scan(conn, old)
    access = SqlAccessRequests(conn)
    for email in ("a@example.com", "b@example.com", pseudonym(uuid.uuid4())):
        access.record(email, at=NOW)
    assert reads(conn).totals(NOW + timedelta(hours=1)) == Totals(
        users=2,
        brands=2,
        archived_brands=1,
        brands_this_week=2,
        scans_today=1,
        searches_this_month=0,
        pending_requests=2,
    )


def test_requests_list_pending_first_and_hide_deleted_accounts(conn: Connection) -> None:
    access = SqlAccessRequests(conn)
    for minutes, email in enumerate(
        ["first@example.com", "second@example.com", "decided@example.com"]
    ):
        access.record(email, at=NOW + timedelta(minutes=minutes))
    access.record(pseudonym(uuid.uuid4()), at=NOW)
    rows = reads(conn).requests()
    decided = next(r for r in rows if r.email == "decided@example.com")
    access.decide(decided.request_id, AccessDecision.REJECTED, at=NOW + timedelta(hours=1))
    access.decide(decided.request_id, AccessDecision.APPROVED, at=NOW + timedelta(hours=2))
    rows = reads(conn).requests()
    assert [(r.email, r.decision) for r in rows] == [
        ("first@example.com", None),
        ("second@example.com", None),
        ("decided@example.com", AccessDecision.APPROVED),  # the latest decision
    ]


def test_brands_show_owner_and_last_scan(conn: Connection) -> None:
    owner = add_user(conn, "owner@example.com")
    brand = add_brand(conn, owner, slug="ola", name="Ola")
    add_scan(conn, brand)
    (row,) = [b for b in reads(conn).brands() if b.name == "Ola"]
    assert (row.owner_email, row.archived, row.last_scan_at is not None) == (
        "owner@example.com",
        False,
        True,
    )

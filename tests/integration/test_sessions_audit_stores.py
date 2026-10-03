"""Sessions and the audit log on real Postgres (ADR-0009, ADR-0013)."""

import uuid
from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address, ip_address

import pytest
from sqlalchemy import Connection, select, update

from serpsense.adapters.db.audit import SqlAuditLog
from serpsense.adapters.db.sessions import SqlSessions
from serpsense.domain.enums import AuditAction, AuditTarget
from serpsense.ports.audit import AuditEntry, Network
from serpsense.ports.sessions import NewSession
from tests.integration.db_helpers import NOW, add_user, table

pytestmark = pytest.mark.integration

MINUTE, HOUR = timedelta(minutes=1), timedelta(hours=1)
IP = ip_address("203.0.113.7")


def session(
    conn: Connection,
    token: bytes,
    *,
    user: uuid.UUID | None = None,
    ip: IPv4Address | IPv6Address | None = None,
    user_agent: str | None = None,
) -> NewSession:
    owner = user or add_user(conn, f"{token.decode()}@example.com")
    return NewSession(owner, token * 32, b"c" * 32, NOW, NOW + HOUR, ip, user_agent)


def test_a_session_opens_until_it_expires_is_revoked_or_its_user_goes(conn: Connection) -> None:
    sessions = SqlSessions(conn)
    new = session(conn, b"t", ip=IP, user_agent="Mozilla\x00" + "x" * 300)
    session_id = sessions.create(new)
    active = sessions.active(b"t" * 32, at=NOW + MINUTE)
    assert active is not None and (active.session_id, active.user_id) == (session_id, new.user_id)
    assert active.csrf_secret == b"c" * 32 and sessions.active(b"x" * 32, at=NOW) is None
    stored = conn.execute(select(table("sessions").c.ip, table("sessions").c.user_agent)).one()
    assert (stored.ip, stored.user_agent) == (IP, "Mozilla" + "x" * 249)  # cleaned, 256 at most
    sessions.extend(session_id, at=NOW, expires_at=NOW + 2 * HOUR)
    sessions.extend(session_id, at=NOW, expires_at=NOW + MINUTE)  # never moved earlier
    assert sessions.active(b"t" * 32, at=NOW + 90 * MINUTE) is not None
    sessions.extend(session_id, at=NOW + 3 * HOUR, expires_at=NOW + 4 * HOUR)  # expired: stays so
    assert sessions.active(b"t" * 32, at=NOW + 3 * HOUR) is None
    sessions.revoke(session_id, at=NOW)
    assert sessions.active(b"t" * 32, at=NOW + MINUTE) is None


def test_revoking_all_closes_every_session_of_the_user(conn: Connection) -> None:
    sessions = SqlSessions(conn)
    first = session(conn, b"a")
    sessions.create(first)
    sessions.create(session(conn, b"b", user=first.user_id))
    other = session(conn, b"c")  # someone else's
    sessions.create(other)
    assert sessions.revoke_all(first.user_id, at=NOW + 2 * HOUR) == 2  # expired ones too
    assert sessions.revoke_all(first.user_id, at=NOW + 2 * HOUR) == 0
    assert sessions.active(b"c" * 32, at=NOW) is not None
    users = table("users")
    conn.execute(update(users).where(users.c.id == other.user_id).values(deleted_at=NOW))
    assert sessions.active(b"c" * 32, at=NOW) is None  # a deleted account opens nothing


def test_an_audit_entry_keeps_its_network_apart(conn: Connection) -> None:
    log, user = SqlAuditLog(conn), add_user(conn)
    target = (AuditTarget.USER, user)
    entry = AuditEntry(
        AuditAction.LOGIN_SUCCEEDED, NOW, user, target, {"new": True}, Network(IP, "UA")
    )
    event_id = log.record(entry)
    log.record(AuditEntry(AuditAction.LOGGED_OUT, NOW, user))  # no network: no row
    log.record(AuditEntry(AuditAction.LOGGED_OUT, NOW, user, network=Network(None, "\x00")))
    events, net = table("audit_events"), table("audit_event_network")
    row = conn.execute(select(events).where(events.c.id == event_id)).one()
    assert (row.action, row.target_type, row.details) == (
        "auth.login_succeeded",
        "user",
        {"new": True},
    )
    assert [tuple(r) for r in conn.execute(select(net.c.audit_event_id, net.c.ip))] == [
        (event_id, IP)
    ]


def test_a_session_ends_thirty_days_after_it_began(conn: Connection) -> None:
    sessions, day = SqlSessions(conn), timedelta(days=1)
    slid = sessions.create(session(conn, b"m"))
    sessions.extend(slid, at=NOW, expires_at=NOW + 40 * day)
    expiry = select(table("sessions").c.expires_at).where(table("sessions").c.id == slid)
    assert conn.execute(expiry).scalar_one() == NOW + 30 * day  # capped
    long = session(conn, b"n")
    sessions.create(NewSession(long.user_id, b"n" * 32, b"c" * 32, NOW, NOW + 40 * day))
    assert sessions.active(b"n" * 32, at=NOW + 29 * day) is not None
    assert sessions.active(b"n" * 32, at=NOW + 30 * day + MINUTE) is None  # too old to open

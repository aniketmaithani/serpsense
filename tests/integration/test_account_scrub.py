"""The account-deletion scrub and the lookups it starts from, on real Postgres (ADR-0013)."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv6Address

import pytest
from sqlalchemy import Connection, Engine, select, update

from serpsense.adapters.db.access import SqlAccessRequests
from serpsense.adapters.db.accounts import SqlAccounts
from serpsense.adapters.db.audit import SqlAuditLog
from serpsense.adapters.db.otp_codes import SqlOtpCodes
from serpsense.adapters.db.outbox import SqlOutbox
from serpsense.adapters.db.scan_store import SqlScanStore
from serpsense.adapters.db.sessions import SqlSessions
from serpsense.domain.enums import AuditAction, AuditTarget, OutboxOutcome
from serpsense.ports.accounts import Scrubbed, pseudonym
from serpsense.ports.audit import AuditEntry, Network
from serpsense.ports.sessions import NewSession
from tests.integration.db_helpers import NOW, add_brand, add_scan, add_user, table, traces
from tests.integration.test_scan_concurrency import race

pytestmark = pytest.mark.integration

HOUR = timedelta(hours=1)
USERS, CODES, BRANDS = table("users"), table("otp_codes"), table("brands")
REQUESTS = table("access_requests")
SCANS, MESSAGES, SESSIONS = table("scans"), table("outbox_messages"), table("sessions")
# Where a person's address, IP and user agent are before the scrub: the scan's positive control.
HELD_AT = [
    [
        "access_requests.email",
        "brands.tone_notes",
        "otp_codes.email",
        "outbox_messages.recipient_email",
        "users.email",
    ],
    ["audit_event_network.ip", "otp_codes.request_ip", "sessions.ip"],
    ["audit_event_network.user_agent", "sessions.user_agent"],
]


@dataclass(frozen=True)
class Person:
    """A user with a row of every kind that holds an address, IP or user agent."""

    user_id: uuid.UUID
    email: str
    ip: IPv6Address
    agent: str
    codes: tuple[uuid.UUID, uuid.UUID]  # used, then live

    @property
    def needles(self) -> tuple[str, str, str]:
        return self.email, str(self.ip), self.agent


def person(conn: Connection, slug: str) -> Person:
    tag = uuid.uuid4().hex
    email, agent = f"{tag[:12]}@example.com", f"Mozilla/5.0 ({tag})"
    ip = IPv6Address((0x20010DB8 << 96) | (uuid.uuid4().int >> 32))
    codes, outbox, network = SqlOtpCodes(conn), SqlOutbox(conn), Network(ip, agent)
    used = _code(codes, outbox, email, ip)  # asked for before the account existed: no user
    codes.consume(used, at=NOW)
    SqlAccessRequests(conn).record(email, at=NOW)  # asked while not invited (ADR-0014)
    user_id = add_user(conn, email)
    live = _code(codes, outbox, email, ip)
    outbox.record(_message(conn, used), OutboxOutcome.SENT, at=NOW)
    for token in ("a", "b"):
        hashed = hashlib.sha256(f"{tag}{token}".encode()).digest()
        SqlSessions(conn).create(NewSession(user_id, hashed, b"c" * 32, NOW, NOW + HOUR, ip, agent))
    audit = SqlAuditLog(conn)
    target = (AuditTarget.OTP_CODE, used)
    audit.record(AuditEntry(AuditAction.CODE_REQUESTED, NOW, target=target, network=network))
    login = AuditEntry(AuditAction.LOGIN_SUCCEEDED, NOW, actor_user_id=user_id, network=network)
    audit.record(login)
    add_scan(conn, add_brand(conn, user_id, slug=slug, tone_notes=f"Ask {email} first"))
    add_brand(conn, user_id, slug=f"{slug}-old", archived_at=NOW - HOUR)
    return Person(user_id, email, ip, agent, (used, live))


def _code(codes: SqlOtpCodes, outbox: SqlOutbox, email: str, ip: IPv6Address) -> uuid.UUID:
    code_id = codes.issue(email, b"h" * 32, at=NOW, expires_at=NOW + HOUR, ip=ip)
    assert code_id is not None
    outbox.add_otp_email(code_id, sealed=b"sealed", minutes=10, at=NOW)
    return code_id


def _message(conn: Connection, code_id: uuid.UUID) -> uuid.UUID:
    query = select(MESSAGES.c.id).where(MESSAGES.c.otp_code_id == code_id)
    return conn.execute(query).scalar_one()


def found(conn: Connection, someone: Person) -> list[list[str]]:
    return [traces(conn, needle) for needle in someone.needles]


def snapshot(conn: Connection, someone: Person) -> list[list[tuple[object, ...]]]:
    """Every row of the person's that the scrub could touch, as it stands."""
    brands = select(BRANDS.c.id).where(BRANDS.c.owner_id == someone.user_id).scalar_subquery()
    queries = [
        select(USERS).where(USERS.c.id == someone.user_id),
        select(BRANDS).where(BRANDS.c.owner_id == someone.user_id),
        select(SCANS.c.id, SCANS.c.status).where(SCANS.c.brand_id.in_(brands)),
        select(CODES).where(CODES.c.id.in_(someone.codes)),
        select(MESSAGES).where(MESSAGES.c.otp_code_id.in_(someone.codes)),
        select(SESSIONS).where(SESSIONS.c.user_id == someone.user_id),
        select(REQUESTS).where(REQUESTS.c.email == someone.email),
    ]
    return [sorted(tuple(row) for row in conn.execute(q.order_by(None))) for q in queries]


def test_the_scrub_leaves_no_address_ip_or_user_agent_and_spares_everyone_else(
    conn: Connection,
) -> None:
    gone, kept = person(conn, "gone"), person(conn, "kept")
    assert found(conn, gone) == found(conn, kept) == HELD_AT
    kept_rows = snapshot(conn, kept)
    sealed = select(MESSAGES.c.sensitive_data_encrypted).where(
        MESSAGES.c.otp_code_id.in_(gone.codes)
    )
    assert b"sealed" in conn.execute(sealed).scalars().all()  # the live code's, still pending
    accounts = SqlAccounts(conn)
    scrubbed = accounts.scrub(gone.user_id, gone.email.upper(), at=NOW + HOUR)  # citext
    assert scrubbed == Scrubbed(brands=2, sessions=2, codes=2, messages=2, network=2, requests=1)
    assert found(conn, gone) == [[], [], []]
    assert conn.execute(sealed).scalars().all() == [None, None]  # bytea: the scan can't see it
    assert found(conn, kept) == HELD_AT and snapshot(conn, kept) == kept_rows
    user = conn.execute(select(USERS).where(USERS.c.id == gone.user_id)).one()
    assert (user.email, user.deleted_at) == (pseudonym(gone.user_id), NOW + HOUR)
    assert accounts.email_of(gone.user_id) is None and accounts.lock(gone.user_id) is None
    assert accounts.lock(kept.user_id) == kept.email
    codes = conn.execute(select(CODES).where(CODES.c.id.in_(gone.codes))).all()
    assert {(c.email, c.code_hash, c.request_ip) for c in codes} == {
        (pseudonym(c.id), bytes(32), None) for c in codes
    }
    assert all(c.consumed_at or c.superseded_at == NOW + HOUR for c in codes)  # none left live
    brands = conn.execute(select(BRANDS).where(BRANDS.c.owner_id == gone.user_id)).all()
    archived = {b.slug: b.archived_at for b in brands}
    assert archived == {"gone": NOW + HOUR, "gone-old": NOW - HOUR}  # the earlier archive stays
    assert all(b.tone_notes is None for b in brands)


def test_the_same_address_can_sign_up_again_as_someone_new(conn: Connection) -> None:
    gone = person(conn, "again")
    accounts = SqlAccounts(conn)
    accounts.scrub(gone.user_id, gone.email, at=NOW + HOUR)
    assert accounts.user_for(gone.email, at=NOW + 2 * HOUR) != gone.user_id
    again = SqlOtpCodes(conn).issue(gone.email, b"n" * 32, at=NOW, expires_at=NOW + HOUR, ip=None)
    assert again is not None  # no live code of the deleted account stands in the way


def test_pending_emails_are_the_users_and_their_addresss(conn: Connection) -> None:
    someone, other = person(conn, "mail"), person(conn, "other")
    outbox = SqlOutbox(conn)
    pending = outbox.pending_for(someone.user_id, someone.email.upper())
    assert pending == [_message(conn, someone.codes[1])]  # the sent one isn't pending
    detached = update(MESSAGES).where(MESSAGES.c.id == pending[0]).values(user_id=None)
    conn.execute(detached)  # a code email from before the account existed names no user
    assert outbox.pending_for(someone.user_id, someone.email) == pending
    assert outbox.pending_for(other.user_id, other.email) == [_message(conn, other.codes[1])]


def test_queued_scans_are_those_of_the_users_brands(conn: Connection) -> None:
    owner, other = add_user(conn, "q@example.com"), add_user(conn, "r@example.com")
    brand, competitor = add_brand(conn, owner, slug="q"), add_brand(conn, owner, slug="q2")
    queued = {add_scan(conn, brand), add_scan(conn, competitor)}
    add_scan(conn, add_brand(conn, owner, slug="q3"), status="running")
    add_scan(conn, add_brand(conn, other, slug="r"))
    assert set(SqlScanStore(conn).queued_for_owner(owner)) == queued


def test_an_email_being_sent_is_waited_for_and_then_not_pending(
    committing_engine: Engine,
) -> None:
    with committing_engine.begin() as setup:
        someone = person(setup, f"s-{uuid.uuid4().hex[:8]}")
        message = _message(setup, someone.codes[1])

    def sending(conn: Connection) -> None:  # as the dispatcher holds a claimed row
        conn.execute(select(MESSAGES.c.id).where(MESSAGES.c.id == message).with_for_update())
        SqlOutbox(conn).record(message, OutboxOutcome.SENT, at=NOW)

    def deleting(conn: Connection) -> list[uuid.UUID]:
        return SqlOutbox(conn).pending_for(someone.user_id, someone.email)

    assert race(committing_engine, sending, deleting) == []

"""Deleting an account, end to end on real Postgres (ADR-0013): a fresh code and the address
typed out, then nothing personal left anywhere, and nobody else touched."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv6Address

import pytest
from sqlalchemy import Connection, Engine, select

from serpsense.adapters.db.accounts import SqlAccounts
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.ports.audit import Network
from serpsense.services.accounts import AccountDeletion, Deletion
from serpsense.services.sessions import CurrentUser, SessionGuard
from tests.integration.db_helpers import NOW, add_brand, add_scan, table, traces
from tests.integration.test_account_scrub import HELD_AT
from tests.integration.test_scan_concurrency import race
from tests.integration.test_sign_in import KEYS, Limiter, Rig, rig

pytestmark = [pytest.mark.integration, pytest.mark.security]

MINUTES = timedelta(minutes=1)
EVENTS, NETWORK_ROWS = table("audit_events"), table("audit_event_network")
SCANS, TRANSITIONS = table("scans"), table("scan_status_transitions")
MESSAGES, CODES, SESSIONS = table("outbox_messages"), table("otp_codes"), table("sessions")
USERS, BRANDS = table("users"), table("brands")


@dataclass
class Account:
    rig: Rig
    deletion: AccountDeletion
    guard: SessionGuard
    network: Network
    user: CurrentUser
    token: str

    @property
    def needles(self) -> tuple[str, str, str]:
        return self.rig.email, str(self.network.ip), str(self.network.user_agent)

    def fresh_code(self) -> str:
        self.rig.clock.at += 2 * MINUTES  # a code a minute at most, per address
        self.deletion.send_code(self.user, self.network)
        return self.rig.code()

    def wrong(self, code: str) -> str:
        return "000000" if code != "000000" else "111111"

    def delete(self, code: str, typed: str | None = None) -> Deletion:
        typed = self.rig.email if typed is None else typed
        return self.deletion.delete(self.user, code, typed, self.network)


def signed_in(engine: Engine, limiter: Limiter | None = None) -> Account:
    r = rig(engine, limiter=limiter)
    tag = uuid.uuid4().hex
    ip = IPv6Address((0x20010DB8 << 96) | (uuid.uuid4().int >> 32))
    network = Network(ip, f"Mozilla/5.0 ({tag})")
    r.service.request_code(r.email, network)
    session = r.service.verify(r.email, r.code(), network)
    assert session is not None
    uow = lambda: SqlUnitOfWork(engine, r.jobs)  # noqa: E731  (a factory, as composition makes)
    guard = SessionGuard(uow, r.clock, KEYS.csrf, session_days=7)
    user = guard.current(session.token)
    assert user is not None
    deletion = AccountDeletion(uow, r.service, r.clock)
    return Account(r, deletion, guard, network, user, session.token)


def found(engine: Engine, account: Account) -> list[list[str]]:
    with engine.connect() as conn:
        return [traces(conn, needle) for needle in account.needles]


def owning(engine: Engine, account: Account) -> uuid.UUID:
    """A brand of the account's, with notes naming its address, and a queued scan of it."""
    with engine.begin() as conn:
        slug, notes = f"b-{uuid.uuid4().hex[:8]}", f"Ask {account.rig.email}"
        return add_scan(conn, add_brand(conn, account.user.user_id, slug=slug, tone_notes=notes))


def rows(engine: Engine, account: Account) -> list[list[tuple[object, ...]]]:
    """Every row of the account's that a deletion could change, as it stands."""
    user_id, email = account.user.user_id, account.rig.email
    brands = select(BRANDS.c.id).where(BRANDS.c.owner_id == user_id).scalar_subquery()
    queries = [
        select(USERS).where(USERS.c.id == user_id),
        select(BRANDS).where(BRANDS.c.owner_id == user_id),
        select(SCANS.c.id, SCANS.c.status).where(SCANS.c.brand_id.in_(brands)),
        select(CODES).where(CODES.c.email == email),
        select(MESSAGES).where(MESSAGES.c.recipient_email == email),
        select(SESSIONS).where(SESSIONS.c.user_id == user_id),
    ]
    with engine.connect() as conn:
        return [sorted(tuple(row) for row in conn.execute(query)) for query in queries]


def test_deleting_leaves_nothing_personal_and_touches_nobody_else(
    committing_engine: Engine,
) -> None:
    gone, kept = signed_in(committing_engine), signed_in(committing_engine)
    scan, _ = owning(committing_engine, gone), owning(committing_engine, kept)
    code = gone.fresh_code()
    assert found(committing_engine, gone) == found(committing_engine, kept) == HELD_AT
    kept_rows = rows(committing_engine, kept)
    with committing_engine.connect() as conn:
        to_them = select(MESSAGES.c.id, MESSAGES.c.user_id, MESSAGES.c.template).where(
            MESSAGES.c.recipient_email == gone.rig.email
        )
        emails = conn.execute(to_them).all()
    assert {(user, template) for _, user, template in emails} == {
        (None, "otp"),  # the sign-in code's, asked for before the account existed
        (gone.user.user_id, "delete_code"),
    }
    assert gone.delete(code, f" {gone.rig.email.upper()} ") is Deletion.DELETED
    assert found(committing_engine, gone) == [[], [], []]
    assert found(committing_engine, kept) == HELD_AT and rows(committing_engine, kept) == kept_rows
    assert gone.guard.current(gone.token) is None and kept.guard.current(kept.token) is not None
    with committing_engine.connect() as conn:
        skipped = select(TRANSITIONS.c.reason, TRANSITIONS.c.actor_user_id).where(
            TRANSITIONS.c.scan_id == scan, TRANSITIONS.c.to_status == "skipped"
        )
        assert conn.execute(skipped).one() == ("account_deleted", gone.user.user_id)
        ids = [message_id for message_id, _, _ in emails]
        statuses = select(MESSAGES.c.status).where(MESSAGES.c.id.in_(ids))
        assert conn.execute(statuses).scalars().all() == ["dropped", "dropped"]  # never sent
        event = conn.execute(
            select(EVENTS).where(
                EVENTS.c.actor_user_id == gone.user.user_id, EVENTS.c.action == "account.deleted"
            )
        ).one()
        network = select(NETWORK_ROWS).where(NETWORK_ROWS.c.audit_event_id == event.id)
        assert conn.execute(network).first() is None
    assert set(event.details) == {"brands", "sessions", "codes", "messages", "network"}


def test_a_wrong_address_or_code_is_a_wrong_guess_and_deletes_nothing(
    committing_engine: Engine,
) -> None:
    account = signed_in(committing_engine)
    code = account.fresh_code()
    before = rows(committing_engine, account)
    assert account.delete(code, "someone@example.com") is Deletion.REFUSED  # the right code
    assert account.delete(account.wrong(code)) is Deletion.REFUSED
    assert rows(committing_engine, account) == before  # nothing deleted, the code still live
    assert account.delete(code) is Deletion.DELETED  # two wrong guesses of five


def test_five_wrong_guesses_lock_deletion_out(committing_engine: Engine) -> None:
    account = signed_in(committing_engine)
    code = account.fresh_code()
    for _ in range(4):
        assert account.delete(account.wrong(code)) is Deletion.REFUSED
    assert account.delete(code, "someone@example.com") is Deletion.REFUSED  # the fifth
    assert account.delete(code) is Deletion.REFUSED  # locked out, even with both right
    assert account.guard.current(account.token) is not None


def test_a_used_code_or_a_rate_limited_source_deletes_nothing(committing_engine: Engine) -> None:
    account = signed_in(committing_engine)
    code = account.fresh_code()
    assert account.rig.service.verify(account.rig.email, code, account.network) is not None
    assert account.delete(code) is Deletion.REFUSED
    for allowed, outcome in ((3, Deletion.REFUSED), (4, Deletion.DELETED)):
        # A sign-in's request and verify, and asking for the code, take three of the source's
        # tries; deleting needs a fourth.
        limited = signed_in(committing_engine, Limiter(left=allowed))
        assert limited.delete(limited.fresh_code()) is outcome


def test_an_email_held_past_the_lock_timeout_makes_deletion_busy(
    committing_engine: Engine, impatient_engine: Engine
) -> None:
    account = signed_in(committing_engine)
    code = account.fresh_code()
    r = account.rig
    impatient = AccountDeletion(lambda: SqlUnitOfWork(impatient_engine, r.jobs), r.service, r.clock)
    before = rows(committing_engine, account)
    to_them = select(MESSAGES.c.id).where(
        MESSAGES.c.recipient_email == r.email, MESSAGES.c.status == "pending"
    )
    with committing_engine.connect() as sender, sender.begin():
        assert sender.execute(to_them.with_for_update()).all()  # a dispatcher sending them
        busy = impatient.delete(account.user, code, r.email, account.network)
    assert busy is Deletion.BUSY
    assert rows(committing_engine, account) == before  # nothing changed, the code still good
    assert account.delete(code) is Deletion.DELETED


def test_two_deletions_at_once_delete_once(committing_engine: Engine) -> None:
    account = signed_in(committing_engine)
    user_id, email = account.user.user_id, account.rig.email

    def first(conn: Connection) -> None:
        accounts = SqlAccounts(conn)
        assert accounts.lock(user_id) == email
        accounts.scrub(user_id, email, at=NOW)

    assert race(committing_engine, first, lambda conn: SqlAccounts(conn).lock(user_id)) is None

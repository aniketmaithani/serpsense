"""Signing in with an emailed code, end to end on real Postgres (ADR-0009)."""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from ipaddress import ip_address
from typing import Any

import pytest
from sqlalchemy import Engine, Select, select

from serpsense.adapters.crypto.fernet_box import FernetBox
from serpsense.adapters.db.access import SqlAccessRequests
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.domain import auth
from serpsense.domain.auth import SignupPolicy
from serpsense.domain.enums import AccessDecision, SignupMode
from serpsense.ports.accounts import InvalidEmail
from serpsense.ports.audit import Network
from serpsense.services.auth import AuthKeys, SignIn, SignInPorts
from tests.factories import TEST_FERNET_KEY
from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW, table

pytestmark = pytest.mark.integration

BOX = FernetBox((TEST_FERNET_KEY,))
KEYS = AuthKeys(b"o" * 32, b"c" * 32)
NETWORK = Network(ip_address("203.0.113.7"), "Mozilla/5.0")
OPEN = SignupPolicy(invite_only=False)
MESSAGES, CODES, EVENTS = table("outbox_messages"), table("otp_codes"), table("audit_events")
REQUESTS = table("access_requests")
INVITE = SignupPolicy(invite_only=True, emails=frozenset({"boss@example.com"}))


@dataclass
class Jobs:
    nudges: int = 0

    def run_scan(self, scan_id: uuid.UUID) -> None:
        raise AssertionError("sign-in queues no scans")

    def dispatch_outbox(self) -> None:
        self.nudges += 1


@dataclass
class Limiter:
    left: int = 1000
    keys: list[str] = field(default_factory=list)

    def allow(self, key: str, *, limit: int, window: timedelta) -> bool:
        self.keys.append(key)
        self.left -= 1
        return self.left >= 0


@dataclass
class Rig:
    service: SignIn
    limiter: Limiter
    clock: FixedClock
    jobs: Jobs
    engine: Engine
    email: str = field(default_factory=lambda: f"{uuid.uuid4().hex[:10]}@example.com")

    def code(self) -> str:
        """The code in the email's latest outbox row, opened as the dispatcher would."""
        with self.engine.connect() as conn:
            query = (
                select(MESSAGES.c.sensitive_data_encrypted)
                .where(MESSAGES.c.recipient_email == self.email)
                .order_by(MESSAGES.c.created_at.desc())
                .limit(1)
            )
            return BOX.open(conn.execute(query).scalar_one()).decode()

    def count(self, query: Select[Any]) -> int:
        with self.engine.connect() as conn:
            return len(conn.execute(query).all())


def rig(
    engine: Engine,
    policy: SignupPolicy = OPEN,
    limiter: Limiter | None = None,
    *,
    email: str | None = None,
    console_on: bool = False,
) -> Rig:
    clock, jobs, counting = FixedClock(NOW), Jobs(), limiter or Limiter()
    ports = SignInPorts(lambda: SqlUnitOfWork(engine, jobs), BOX, counting, clock)
    service = SignIn(ports, KEYS, policy, session_days=7, follow_switches=console_on)
    if email is None:
        return Rig(service, counting, clock, jobs, engine)
    return Rig(service, counting, clock, jobs, engine, email)


def test_a_code_signs_in_once_and_opens_a_session(committing_engine: Engine) -> None:
    r = rig(committing_engine)
    r.service.request_code(f"  {r.email.upper()}  ", NETWORK)
    assert r.jobs.nudges == 1  # the email goes out now, not at the next Beat
    code = r.code()
    assert r.service.verify(r.email, "000000" if code != "000000" else "111111", NETWORK) is None
    signed_in = r.service.verify(r.email, code, NETWORK)
    assert signed_in is not None and len(signed_in.token) >= 43
    assert r.service.verify(r.email, code, NETWORK) is None  # used once
    sessions = table("sessions")
    opened = select(sessions.c.user_id).where(
        sessions.c.token_hash == auth.token_hash(signed_in.token)
    )
    with committing_engine.connect() as conn:
        assert conn.execute(opened).scalar_one() == signed_in.user_id  # kept only as a hash
    actions = select(EVENTS.c.action).where(EVENTS.c.target_id == signed_in.user_id)
    with committing_engine.connect() as conn:
        assert conn.execute(actions).scalars().all() == ["auth.login_succeeded"]
    stored = select(CODES.c.code_hash).where(CODES.c.email == r.email)
    with committing_engine.connect() as conn:
        assert code.encode() not in conn.execute(stored).scalar_one()  # kept only as an HMAC


def sent_to(r: Rig, email: str) -> int:
    return r.count(select(MESSAGES.c.id).where(MESSAGES.c.recipient_email == email))


def test_asking_for_a_code_reveals_nothing(committing_engine: Engine) -> None:
    invite = SignupPolicy(invite_only=True, domains=frozenset({"serpsense.in"}))
    r = rig(committing_engine, invite)
    r.service.request_code(r.email, NETWORK)  # not invited: the same quiet answer
    limited = rig(committing_engine, limiter=Limiter(left=0))
    limited.service.request_code(limited.email, NETWORK)  # rate limited: the same
    sent = rig(committing_engine)
    sent.service.request_code(sent.email, NETWORK)
    sent.service.request_code(sent.email, NETWORK)  # a second ask within the minute: nothing new
    assert [sent_to(x, x.email) for x in (r, limited, sent)] == [0, 0, 1]
    codes = select(CODES.c.id).where(CODES.c.email.in_([r.email, limited.email]))
    assert r.count(codes) == 0  # refused before any code existed
    assert limited.limiter.keys == ["otp-request:203.0.113.7"]
    with pytest.raises(InvalidEmail):
        sent.service.request_code("not an address", NETWORK)


def requests_from(r: Rig, email: str) -> Select[Any]:
    return select(REQUESTS.c.id).where(REQUESTS.c.email == email)


def decide(r: Rig, email: str, decision: AccessDecision, minutes: int = 0) -> None:
    with r.engine.begin() as conn:
        request_id = conn.execute(requests_from(r, email)).scalar_one()
        at = NOW + timedelta(minutes=minutes)
        assert SqlAccessRequests(conn).decide(request_id, decision, at=at)


def test_an_uninvited_address_asks_and_is_let_in_once_approved(committing_engine: Engine) -> None:
    r = rig(committing_engine, INVITE)
    r.service.request_code(r.email.upper(), NETWORK)
    r.service.request_code(r.email, NETWORK)  # asking again: still one request
    assert sent_to(r, r.email) == 0 and r.count(requests_from(r, r.email)) == 1
    decide(r, r.email, AccessDecision.APPROVED)
    r.service.request_code(r.email, NETWORK)
    assert r.service.verify(r.email, r.code(), NETWORK) is not None  # in, like an invited one
    for invited in (
        rig(committing_engine, INVITE, email="boss@example.com"),
        rig(committing_engine),
    ):
        invited.service.request_code(invited.email, NETWORK)  # invited, or open mode: no request
        assert sent_to(invited, invited.email) >= 1
        assert invited.count(requests_from(invited, invited.email)) == 0


def test_new_access_requests_are_capped_each_hour(committing_engine: Engine) -> None:
    r = rig(committing_engine, INVITE)
    r.clock.at = NOW - timedelta(days=30)  # an hour of its own: no other test's requests count
    with committing_engine.begin() as conn:
        access = SqlAccessRequests(conn)
        taken = access.recorded_between(r.clock.at - auth.HOUR, r.clock.at)
        room = auth.ACCESS_REQUESTS_PER_HOUR_IN_ALL - taken
        for _ in range(room - 1):
            access.record(f"{uuid.uuid4().hex[:10]}@example.com", at=r.clock.at)
    r.service.request_code(r.email, NETWORK)  # the last one this hour
    late = rig(committing_engine, INVITE)
    late.clock.at = r.clock.at
    late.service.request_code(late.email, NETWORK)  # the same quiet answer, but not recorded
    assert r.count(requests_from(r, r.email)) == 1
    assert late.count(requests_from(late, late.email)) == 0


def switch(engine: Engine, mode: SignupMode, minutes: int = 0) -> None:
    with engine.begin() as conn:  # the operator's switch from the console (ADR-0015)
        at = NOW + timedelta(minutes=minutes)
        SqlAccessRequests(conn).switch_signup_mode(mode, at=at)


def test_opened_by_the_operator_anyone_signs_in_and_stays_in(fresh_engine: Engine) -> None:
    r = rig(fresh_engine, INVITE, console_on=True)  # the environment says invite
    switch(fresh_engine, SignupMode.OPEN)
    assert not r.service.invite_only()
    assert r.service.request_code(r.email, NETWORK) is False  # nobody waits for the operator
    assert r.service.verify(r.email, r.code(), NETWORK) is not None
    switch(fresh_engine, SignupMode.INVITE, minutes=1)  # closed again
    r.clock.at = NOW + timedelta(minutes=2)
    assert r.service.request_code(r.email, NETWORK) is True
    assert r.service.verify(r.email, r.code(), NETWORK) is not None  # approved as it signed up


def test_closing_stops_a_code_sent_while_open(fresh_engine: Engine) -> None:
    r = rig(fresh_engine, INVITE, console_on=True)
    switch(fresh_engine, SignupMode.OPEN)
    r.service.request_code(r.email, NETWORK)
    code = r.code()
    switch(fresh_engine, SignupMode.INVITE, minutes=1)  # before they used it
    assert r.service.verify(r.email, code, NETWORK) is None


def test_closed_by_the_operator_an_uninvited_address_waits(fresh_engine: Engine) -> None:
    r = rig(fresh_engine, console_on=True)  # the environment says open
    assert not r.service.invite_only()
    switch(fresh_engine, SignupMode.INVITE)
    assert r.service.request_code(r.email, NETWORK) is True
    assert sent_to(r, r.email) == 0 and r.count(requests_from(r, r.email)) == 1


def test_with_the_console_off_the_environments_mode_holds(fresh_engine: Engine) -> None:
    switch(fresh_engine, SignupMode.OPEN)  # switched while the console was on
    r = rig(fresh_engine, INVITE)  # then ADMIN_PASSWORD was removed: the brake (ADR-0015)
    assert r.service.request_code(r.email, NETWORK) is True
    assert sent_to(r, r.email) == 0


def test_a_rejection_stops_a_code_already_sent(committing_engine: Engine) -> None:
    r = rig(committing_engine, INVITE)
    r.service.request_code(r.email, NETWORK)
    decide(r, r.email, AccessDecision.APPROVED)
    r.service.request_code(r.email, NETWORK)
    code = r.code()
    decide(r, r.email, AccessDecision.REJECTED, minutes=1)  # the latest decision counts
    assert r.service.verify(r.email, code, NETWORK) is None


def test_verifying_is_limited_too(committing_engine: Engine) -> None:
    r = rig(committing_engine)
    r.service.request_code(r.email, NETWORK)
    code = r.code()
    assert r.service.verify("not an address", code, NETWORK) is None
    invite = rig(committing_engine, SignupPolicy(invite_only=True))
    assert invite.service.verify(r.email, code, NETWORK) is None  # not invited, whatever the code
    limited = rig(committing_engine, limiter=Limiter(left=0))
    assert limited.service.verify(r.email, code, NETWORK) is None
    assert r.service.verify(r.email, code, NETWORK) is not None  # none of those spent the code
    v6 = rig(committing_engine)
    v6.service.verify(v6.email, "123456", Network(ip_address("2001:db8:1:2::abcd"), None))
    assert v6.limiter.keys == ["otp-verify:2001:db8:1:2::/64"]  # a /64, not one address


def test_a_flood_across_addresses_stops_at_the_global_cap(
    committing_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    with committing_engine.connect() as conn:
        issued = len(conn.execute(select(CODES.c.id).where(CODES.c.created_at >= NOW)).all())
    monkeypatch.setattr(auth, "CODES_PER_HOUR_IN_ALL", issued + 1)
    first, second = rig(committing_engine), rig(committing_engine)
    first.service.request_code(first.email, NETWORK)
    second.service.request_code(second.email, NETWORK)
    assert (sent_to(first, first.email), sent_to(second, second.email)) == (1, 0)


def test_five_wrong_guesses_or_ten_minutes_end_a_code(committing_engine: Engine) -> None:
    r = rig(committing_engine)
    r.service.request_code(r.email, NETWORK)
    code = r.code()
    wrong = "999999" if code != "999999" else "888888"
    for _ in range(5):
        assert r.service.verify(r.email, wrong, NETWORK) is None
    assert r.service.verify(r.email, code, NETWORK) is None  # locked out, even with the code
    four = rig(committing_engine)
    four.service.request_code(four.email, NETWORK)
    for _ in range(4):
        four.service.verify(four.email, "000000" if four.code() != "000000" else "111111", NETWORK)
    assert four.service.verify(four.email, four.code(), NETWORK) is not None  # four aren't five
    late = rig(committing_engine)
    late.service.request_code(late.email, NETWORK)
    late.clock.at = NOW + timedelta(minutes=10)
    assert late.service.verify(late.email, late.code(), NETWORK) is None

"""Signing in with an emailed code, end to end on real Postgres (ADR-0009)."""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from ipaddress import ip_address
from typing import Any

import pytest
from sqlalchemy import Engine, Select, select

from serpsense.adapters.crypto.fernet_box import FernetBox
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.domain import auth
from serpsense.domain.auth import SignupPolicy
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


def rig(engine: Engine, policy: SignupPolicy = OPEN, limiter: Limiter | None = None) -> Rig:
    clock, jobs, counting = FixedClock(NOW), Jobs(), limiter or Limiter()
    ports = SignInPorts(lambda: SqlUnitOfWork(engine, jobs), BOX, counting, clock)
    return Rig(SignIn(ports, KEYS, policy, session_days=7), counting, clock, jobs, engine)


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

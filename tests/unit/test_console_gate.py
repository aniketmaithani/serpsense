"""The operator console's door: password, limits, audit, session (ADR-0014)."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from types import TracebackType
from typing import Self

import pytest
from structlog.testing import capture_logs

from serpsense.domain import console
from serpsense.domain.enums import AuditAction
from serpsense.ports.audit import AuditEntry, Network
from serpsense.services.console import ConsoleGate, GatePorts
from tests.fakes import FixedClock

pytestmark = [pytest.mark.unit, pytest.mark.security]

AT = datetime(2026, 10, 4, 12, tzinfo=UTC)
PASSWORD = "correct horse battery"
KEYS = console.console_keys(b"k" * 32, PASSWORD)
HERE = Network(ip_address("203.0.113.9"), "Mozilla/5.0")


@dataclass
class Limiter:
    allowed: dict[str, int] = field(default_factory=dict)  # key → how many it lets through
    asked: list[str] = field(default_factory=list)

    def allow(self, key: str, *, limit: int, window: timedelta) -> bool:
        self.asked.append(key)
        left = self.allowed.get(key, limit) - 1
        self.allowed[key] = left
        return left >= 0


@dataclass
class Audit:
    entries: list[AuditEntry] = field(default_factory=list)

    def record(self, entry: AuditEntry) -> uuid.UUID:
        self.entries.append(entry)
        return uuid.uuid4()

    def operator_events_since(self, action: AuditAction, since: datetime) -> int:
        return sum(e.action is action and e.at >= since for e in self.entries)

    def last_operator_event(self, action: AuditAction) -> datetime | None:
        return max((e.at for e in self.entries if e.action is action), default=None)


@dataclass
class Work:
    audit: Audit = field(default_factory=Audit)

    def __call__(self) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def gate(limiter: Limiter | None = None) -> tuple[ConsoleGate, Work, FixedClock]:
    work, clock = Work(), FixedClock(AT)
    ports = GatePorts(work, limiter or Limiter(), clock)
    return ConsoleGate(ports, KEYS), work, clock


def test_the_password_opens_a_four_hour_session() -> None:
    door, work, clock = gate()
    with capture_logs() as logs:
        token = door.log_in(PASSWORD, HERE)
    assert token is not None and door.signed_in(token)
    assert PASSWORD not in str(logs) and token not in str(logs)
    assert [e.action for e in work.audit.entries] == [AuditAction.ADMIN_LOGIN_SUCCEEDED]
    assert work.audit.entries[0].network is None  # the operator isn't a user: no network row
    clock.at = AT + console.SESSION_LENGTH
    assert not door.signed_in(token)
    assert not door.signed_in(None) and not door.signed_in("")


def test_a_wrong_password_is_audited_and_opens_nothing() -> None:
    door, work, _ = gate()
    with capture_logs() as logs:
        assert door.log_in("guess", HERE) is None
    assert [log["event"] for log in logs] == ["admin.login_failed"]
    assert [e.action for e in work.audit.entries] == [AuditAction.ADMIN_LOGIN_FAILED]


def test_attempts_are_limited_per_source_and_overall() -> None:
    per_source = Limiter({"admin-login:203.0.113.9": 0})
    door, work, _ = gate(per_source)
    with capture_logs() as logs:
        assert door.log_in(PASSWORD, HERE) is None  # even the right password
    assert [log["event"] for log in logs] == ["admin.login_limited"] and not work.audit.entries
    overall = Limiter({"admin-login:all": 0})
    door, _, _ = gate(overall)
    assert door.log_in(PASSWORD, HERE) is None
    assert overall.asked == ["admin-login:203.0.113.9", "admin-login:all"]
    v6 = Limiter()
    door, _, _ = gate(v6)
    door.log_in("guess", Network(ip_address("2001:db8:1:2::abcd"), None))
    assert v6.asked[0] == "admin-login:2001:db8:1:2::/64"


def test_failures_are_capped_in_postgres_when_redis_lets_everything_through() -> None:
    open_limiter = Limiter({"admin-login:203.0.113.9": 10**6, "admin-login:all": 10**6})
    door, _, clock = gate(open_limiter)  # it never refuses, as when Redis is down
    for minute in range(console.LOGINS_IN_ALL):
        clock.at = AT + timedelta(minutes=minute)
        assert door.log_in("guess", HERE) is None
    with capture_logs() as logs:
        assert door.log_in(PASSWORD, HERE) is None  # even the right password, for the hour
    assert [log["event"] for log in logs] == ["admin.login_limited"]
    clock.at = AT + console.IN_ALL_WINDOW + timedelta(minutes=1)  # the first failures age out
    assert door.log_in(PASSWORD, HERE) is not None


def test_logging_out_ends_every_session_begun_before_it() -> None:
    door, work, clock = gate()
    first, second = door.log_in(PASSWORD, HERE), door.log_in(PASSWORD, HERE)
    clock.at = AT + timedelta(minutes=5)
    door.log_out()
    assert not door.signed_in(first) and not door.signed_in(second)  # copied cookies too
    assert work.audit.entries[-1].action is AuditAction.ADMIN_LOGGED_OUT
    clock.at = AT + timedelta(minutes=6)
    assert door.signed_in(door.log_in(PASSWORD, HERE))  # a new session after it


def test_console_forms_carry_the_sessions_csrf_token() -> None:
    door, _, _ = gate()
    token = door.log_in(PASSWORD, HERE)
    assert token is not None
    assert door.csrf_valid(token, door.csrf_token(token))
    assert not door.csrf_valid(token, "forged")

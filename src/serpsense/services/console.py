"""The operator console's door (ADR-0014).

One password from the environment, compared as HMACs in constant time; attempts limited per
source and across all sources; every success and failure an audit event with no actor and no
network row (the operator isn't a user); the failures are also counted in Postgres, which holds
when the Redis limiter can't. A success opens a session that lasts four hours and is kept only in
a signed cookie; logging out ends every session begun before it. Passwords, cookies and CSRF
tokens are never logged.

Behind the door, the console shows totals, access requests and brands across every user, and
records the operator's decisions on access requests; addresses are never logged.
"""

import secrets
import uuid
from dataclasses import dataclass

from serpsense.domain import auth, console
from serpsense.domain.enums import AccessDecision, AuditAction
from serpsense.observability import get_logger
from serpsense.ports.audit import AuditEntry, Network
from serpsense.ports.clock import Clock
from serpsense.ports.console import BrandRow, ConsoleReads, RequestRow, Totals
from serpsense.ports.rate_limiter import RateLimiter
from serpsense.ports.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)


@dataclass(frozen=True)
class GatePorts:
    unit_of_work: UnitOfWorkFactory
    limiter: RateLimiter
    clock: Clock


class ConsoleGate:
    def __init__(self, ports: GatePorts, keys: console.ConsoleKeys) -> None:
        self._ports, self._keys = ports, keys

    def log_in(self, password: str, network: Network) -> str | None:
        """A session for the right password; None for anything else, all alike."""
        if not self._may_try(network):
            return None
        at = self._ports.clock.now()
        matched = console.password_matches(self._keys, password)
        action = AuditAction.ADMIN_LOGIN_SUCCEEDED if matched else AuditAction.ADMIN_LOGIN_FAILED
        with self._ports.unit_of_work() as uow:
            uow.audit.record(AuditEntry(action, at))
        if not matched:
            log.warning("admin.login_failed")
            return None
        log.info("admin.login_succeeded")
        expires_at = at + console.SESSION_LENGTH
        return console.session_token(self._keys, expires_at, secrets.token_urlsafe(16))

    def signed_in(self, token: str | None) -> bool:
        """A valid session not ended by a logout; the database is asked only about a token
        this key signed."""
        at = self._ports.clock.now()
        if not token or not console.session_valid(self._keys, token, at):
            return False
        with self._ports.unit_of_work() as uow:
            ended = uow.audit.last_operator_event(AuditAction.ADMIN_LOGGED_OUT)
        return console.session_valid(self._keys, token, at, ended_at=ended)

    def log_out(self) -> None:
        """End every console session, copied cookies included."""
        with self._ports.unit_of_work() as uow:
            uow.audit.record(AuditEntry(AuditAction.ADMIN_LOGGED_OUT, self._ports.clock.now()))
        log.info("admin.logged_out")

    def csrf_token(self, token: str) -> str:
        return console.csrf_token(self._keys, token)

    def csrf_valid(self, token: str, submitted: str) -> bool:
        return console.csrf_valid(self._keys, token, submitted)

    def _may_try(self, network: Network) -> bool:
        limiter = self._ports.limiter
        source = f"admin-login:{auth.request_source(network.ip)}"
        if (
            limiter.allow(source, limit=console.LOGINS_PER_SOURCE, window=console.PER_SOURCE_WINDOW)
            and limiter.allow(
                "admin-login:all", limit=console.LOGINS_IN_ALL, window=console.IN_ALL_WINDOW
            )
            and self._failures_below_cap()
        ):
            return True
        log.warning("admin.login_limited")
        return False

    def _failures_below_cap(self) -> bool:
        """The overall cap again, from the audit log: it holds when Redis is down."""
        since = self._ports.clock.now() - console.IN_ALL_WINDOW
        with self._ports.unit_of_work() as uow:
            failures = uow.audit.operator_events_since(AuditAction.ADMIN_LOGIN_FAILED, since)
        return failures < console.LOGINS_IN_ALL


@dataclass(frozen=True)
class Snapshot:
    totals: Totals
    requests: list[RequestRow]
    brands: list[BrandRow]


class Console:
    """The console behind its door: what it shows, and the operator's decisions."""

    def __init__(
        self,
        gate: ConsoleGate,
        reads: ConsoleReads,
        unit_of_work: UnitOfWorkFactory,
        clock: Clock,
    ) -> None:
        self.gate, self._reads = gate, reads
        self._unit_of_work, self._clock = unit_of_work, clock

    def snapshot(self) -> Snapshot:
        at = self._clock.now()
        return Snapshot(self._reads.totals(at), self._reads.requests(), self._reads.brands())

    def decide(self, request_id: uuid.UUID, decision: AccessDecision) -> bool:
        """Approve or reject a request; False if there is no such request. Rejecting stops
        future sign-ins; it doesn't end sessions already open (ADR-0014)."""
        with self._unit_of_work() as uow:
            known = uow.access.decide(request_id, decision, at=self._clock.now())
        if known:
            log.info("access.decided", access_request_id=str(request_id), decision=decision)
        return known

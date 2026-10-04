"""The operator console's door (ADR-0014).

One password from the environment, compared as HMACs in constant time; attempts limited per
source and across all sources; every success and failure an audit event with no actor and no
network row (the operator isn't a user). A success opens a session that lasts four hours and is
kept only in a signed cookie. Passwords, cookies and CSRF tokens are never logged.
"""

import secrets
from dataclasses import dataclass

from serpsense.domain import auth, console
from serpsense.domain.enums import AuditAction
from serpsense.observability import get_logger
from serpsense.ports.audit import AuditEntry, Network
from serpsense.ports.clock import Clock
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
        if not token:
            return False
        return console.session_valid(self._keys, token, self._ports.clock.now())

    def csrf_token(self, token: str) -> str:
        return console.csrf_token(self._keys, token)

    def csrf_valid(self, token: str, submitted: str) -> bool:
        return console.csrf_valid(self._keys, token, submitted)

    def _may_try(self, network: Network) -> bool:
        limiter = self._ports.limiter
        source = f"admin-login:{auth.request_source(network.ip)}"
        if limiter.allow(
            source, limit=console.LOGINS_PER_SOURCE, window=console.PER_SOURCE_WINDOW
        ) and limiter.allow(
            "admin-login:all", limit=console.LOGINS_IN_ALL, window=console.IN_ALL_WINDOW
        ):
            return True
        log.warning("admin.login_limited")
        return False

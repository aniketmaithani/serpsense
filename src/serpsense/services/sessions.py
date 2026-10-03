"""A signed-in visitor's session (ADR-0009).

The cookie's token opens a session that isn't revoked, expired or older than 30 days, and whose
user still exists; its expiry slides forward at most once an hour. Every signed-in write carries
the session's CSRF token. Signing out revokes the session, or every session of the user; both are
audited.
"""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from serpsense.domain import auth
from serpsense.domain.enums import AuditAction, AuditTarget
from serpsense.ports.audit import AuditEntry, Network
from serpsense.ports.clock import Clock
from serpsense.ports.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True)
class CurrentUser:
    """The signed-in principal every user-scoped read and write takes."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    csrf_secret: bytes = field(repr=False)


class SessionGuard:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        clock: Clock,
        csrf_key: bytes,
        *,
        session_days: int,
    ) -> None:
        self._unit_of_work, self._clock, self._csrf_key = unit_of_work, clock, csrf_key
        self._session_length = timedelta(days=session_days)

    def current(self, token: str | None) -> CurrentUser | None:
        """The session a cookie's token opens, its expiry slid forward at most once an hour."""
        if not token:
            return None
        at = self._clock.now()
        with self._unit_of_work() as uow:
            session = uow.sessions.active(auth.token_hash(token), at=at)
            if session is None:
                return None
            if at + self._session_length - session.expires_at >= auth.SESSION_REFRESH:
                uow.sessions.extend(session.session_id, at=at, expires_at=at + self._session_length)
        return CurrentUser(session.user_id, session.session_id, session.csrf_secret)

    def csrf_token(self, user: CurrentUser) -> str:
        return auth.csrf_token(self._csrf_key, user.csrf_secret)

    def csrf_valid(self, user: CurrentUser, token: str) -> bool:
        return auth.csrf_valid(self._csrf_key, user.csrf_secret, token)

    def log_out(self, user: CurrentUser, network: Network) -> None:
        at = self._clock.now()
        with self._unit_of_work() as uow:
            uow.sessions.revoke(user.session_id, at=at)
            target = (AuditTarget.SESSION, user.session_id)
            uow.audit.record(
                AuditEntry(
                    AuditAction.LOGGED_OUT,
                    at,
                    actor_user_id=user.user_id,
                    target=target,
                    network=network,
                )
            )

    def log_out_everywhere(self, user: CurrentUser, network: Network) -> None:
        at = self._clock.now()
        with self._unit_of_work() as uow:
            revoked = uow.sessions.revoke_all(user.user_id, at=at)
            target = (AuditTarget.USER, user.user_id)
            details = {"sessions": revoked}
            entry = AuditEntry(
                AuditAction.SESSIONS_REVOKED,
                at,
                actor_user_id=user.user_id,
                target=target,
                details=details,
                network=network,
            )
            uow.audit.record(entry)

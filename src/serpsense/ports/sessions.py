"""Port for sign-in sessions (ADR-0009): server-side rows; the cookie's token is kept only as its
SHA-256. A store works inside the caller's unit of work and never commits."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Protocol


@dataclass(frozen=True)
class NewSession:
    user_id: uuid.UUID
    token_hash: bytes
    csrf_secret: bytes
    at: datetime
    expires_at: datetime
    ip: IPv4Address | IPv6Address | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class ActiveSession:
    session_id: uuid.UUID
    user_id: uuid.UUID
    csrf_secret: bytes
    expires_at: datetime


class Sessions(Protocol):
    def create(self, new: NewSession) -> uuid.UUID: ...

    def active(self, token_hash: bytes, *, at: datetime) -> ActiveSession | None:
        """The session the token opens: not revoked, not expired, at most 30 days old, its user
        not deleted."""
        ...

    def extend(self, session_id: uuid.UUID, *, at: datetime, expires_at: datetime) -> None:
        """Move a live session's expiry later, but never past 30 days from its start; an expired
        or revoked one stays closed."""
        ...

    def revoke(self, session_id: uuid.UUID, *, at: datetime) -> None: ...

    def revoke_all(self, user_id: uuid.UUID, *, at: datetime) -> int:
        """Revoke every session of the user not revoked yet; how many."""
        ...

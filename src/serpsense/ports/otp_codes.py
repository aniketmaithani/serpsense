"""Port for sign-in codes (ADR-0009, data-model §1): stored hashed, one live per email.

A store works inside the caller's unit of work and never commits. Verification locks the live
code, so parallel guesses queue behind each other and can't beat the attempt limit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Protocol


@dataclass(frozen=True)
class LiveCode:
    code_id: uuid.UUID
    code_hash: bytes
    expires_at: datetime
    failed_attempts: int


class OtpCodes(Protocol):
    def lock_requests(self, email: str) -> None:
        """Hold the email's requests until the unit of work ends, so two can't both pass the
        request limits."""
        ...

    def issued_since(self, email: str, since: datetime) -> list[datetime]:
        """When the email's codes were issued since `since`, for the request limits."""
        ...

    def issued_in_all_since(self, since: datetime) -> int:
        """How many codes were issued since `since`, for every address together."""
        ...

    def issue(
        self,
        email: str,
        code_hash: bytes,
        *,
        at: datetime,
        expires_at: datetime,
        ip: IPv4Address | IPv6Address | None,
    ) -> uuid.UUID | None:
        """Supersede the email's live code and store this one; None when another request issued
        one at the same moment."""
        ...

    def lock_live(self, email: str) -> LiveCode | None:
        """The email's live code, locked until the unit of work ends, with its failed attempts
        counted after the lock (so a guess that waited sees the ones before it); None when
        there is none."""
        ...

    def attempt(self, code_id: uuid.UUID, *, succeeded: bool, at: datetime) -> None: ...

    def consume(self, code_id: uuid.UUID, *, at: datetime) -> None: ...

"""Port for sealing a short secret at rest (ADR-0010: the OTP in an outbox email)."""

from typing import Protocol


class SealBroken(ValueError):
    """The sealed value was tampered with, or no current key opens it."""


class SecretBox(Protocol):
    def seal(self, plaintext: bytes) -> bytes: ...

    def open(self, sealed: bytes) -> bytes:
        """The plaintext; raises SealBroken."""
        ...

"""Port for sending one email (ADR-0010). Only the outbox dispatcher sends."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str  # plain text; no HTML, so nothing to escape


class MailFailed(Exception):
    """The email wasn't accepted. `code` is safe to store and log (no address, no content)."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code, self.retryable = code, retryable


class Mailer(Protocol):
    def send(self, email: Email) -> None:
        """Hand the email to the mail server; raises MailFailed when it isn't accepted."""
        ...

"""Port for user accounts (data-model §1, ADR-0009).

A user is created on their first successful sign-in, or by an operator seeding a demo. Email
addresses are personal data: they live only in `users`, which account deletion scrubs
(ADR-0013), and are never logged.
"""

import re
import uuid
from datetime import datetime
from typing import Protocol

MAX_EMAIL = 254  # RFC 5321's path limit
# One bare address: no list or display-name characters an SMTP server would fan out on, the same
# rule as the outbox's recipient check.
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


class InvalidEmail(ValueError):
    """Not an address a user can have."""


def email_address(raw: str) -> str:
    """An address as it is stored: trimmed, at most 254 characters, no control characters, and
    never in the reserved `.invalid` domain account deletion uses for pseudonyms (ADR-0013)."""
    email = raw.strip()
    if len(email) > MAX_EMAIL or not EMAIL.fullmatch(email) or not email.isprintable():
        raise InvalidEmail("not an email address")
    if email.lower().endswith(".invalid"):
        raise InvalidEmail("a reserved address")
    return email


class Accounts(Protocol):
    def user_for(self, email: str, *, at: datetime) -> uuid.UUID:
        """The user with this email (ignoring case), created if there is none. Raises
        InvalidEmail for anything `email_address` refuses."""
        ...

"""Port for user accounts (data-model §1, ADR-0009, ADR-0013).

A user is created on their first successful sign-in, or by an operator seeding a demo. Email
addresses are personal data: they live only in `users`, which account deletion scrubs
(ADR-0013), and are never logged. Deleting an account keeps its row, under a pseudonym, since
ledgers and the audit log refer to it.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

MAX_EMAIL = 254  # RFC 5321's path limit
# One bare address: no list or display-name characters an SMTP server would fan out on, the same
# rule as the outbox's recipient check.
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
PSEUDONYM_DOMAIN = "serpsense.invalid"  # RFC 2606: no one can sign in with an address in it


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


def pseudonym(row_id: uuid.UUID) -> str:
    """What replaces an address on a deleted account's rows: unique per row, so pseudonyms never
    collide, and in a reserved domain (RFC 2606) no one can sign in with (ADR-0013)."""
    return f"deleted+{row_id}@{PSEUDONYM_DOMAIN}"


@dataclass(frozen=True)
class Scrubbed:
    """How many rows an account's deletion changed, per table, for its audit event."""

    brands: int
    sessions: int
    codes: int
    messages: int
    network: int
    requests: int


class Accounts(Protocol):
    def user_for(self, email: str, *, at: datetime) -> uuid.UUID:
        """The user with this email (ignoring case), created if there is none. Raises
        InvalidEmail for anything `email_address` refuses."""
        ...

    def email_of(self, user_id: uuid.UUID) -> str | None:
        """The address of a user whose account isn't deleted; None otherwise."""
        ...

    def lock(self, user_id: uuid.UUID) -> str | None:
        """Like `email_of`, with the user's row locked until the unit of work ends, so two
        deletions of one account queue and the second finds it gone."""
        ...

    def scrub(self, user_id: uuid.UUID, email: str, *, at: datetime) -> Scrubbed:
        """Delete the account (ADR-0013): its brands archived and their tone notes deleted, its
        sessions deleted, the network details of its audit events (and of its address's
        pre-login events) deleted, every code issued to its address, every email to it or its
        address and its address's access request pseudonymised (a live code superseded, its hash
        zeroed, its IP and sealed content gone), and the user marked deleted under a pseudonym.
        Pending emails are dropped and queued scans skipped by the caller first, through their
        own stores."""
        ...

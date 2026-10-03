"""Port for the email outbox (ADR-0010, data-model §8).

Business code writes a message in the same unit of work as what caused it; the dispatcher claims
one due message at a time, locked until its unit of work ends, so two dispatchers never send the
same message, and records each attempt with the status it implies.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import OutboxKind, OutboxOutcome, OutboxStatus


@dataclass(frozen=True)
class DueEmail:
    message_id: uuid.UUID
    kind: OutboxKind
    recipient: str
    template: str
    data: Mapping[str, str]  # the non-sensitive template fields
    sealed: bytes | None = None  # a sealed secret (a sign-in code), opened only to send it
    stale: bool = False  # its sign-in code expired, was used or was replaced: drop, don't send


class Outbox(Protocol):
    def add_alert_email(
        self, alert_id: uuid.UUID, *, data: Mapping[str, str], at: datetime
    ) -> bool:
        """Queue the alert's email to its brand's owner, due at once; False when it was queued
        already, or the owner's account is gone."""
        ...

    def add_otp_email(
        self, otp_code_id: uuid.UUID, *, sealed: bytes, minutes: int, at: datetime
    ) -> bool:
        """Queue a sign-in code's email to the address it was issued for, with the code sealed;
        False when it was queued already."""
        ...

    def claim_due(self, at: datetime) -> DueEmail | None:
        """The pending message due longest, locked until the unit of work ends; messages that
        another dispatcher holds are skipped. A sign-in code's email gets a two-minute head start
        (so codes go first without starving alerts), and one whose code went stale is claimed at
        once to be dropped. None when nothing is due."""
        ...

    def pending_for(self, user_id: uuid.UUID, email: str) -> list[uuid.UUID]:
        """The pending emails to a user or their address, the code emails among them found
        through the address's codes too (sent before the account existed, so naming no user):
        the rows the deletion scrub pseudonymises. Locked until the unit of work ends: one being
        sent is waited for, and is then no longer pending."""
        ...

    def record(
        self,
        message_id: uuid.UUID,
        outcome: OutboxOutcome,
        *,
        at: datetime,
        error_code: str | None = None,
    ) -> OutboxStatus:
        """Add the attempt, and set the message's status (and next attempt) it implies. Only a
        pending message takes an attempt; any other raises."""
        ...

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


class Outbox(Protocol):
    def claim_due(self, at: datetime) -> DueEmail | None:
        """The pending message due longest, locked until the unit of work ends; messages that
        another dispatcher holds are skipped. None when nothing is due."""
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

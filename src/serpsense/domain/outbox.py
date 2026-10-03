"""An outbox email's status and retry schedule (ADR-0010, data-model §8).

The status follows from the message's send attempts: none, or fewer than 8 retryable errors, is
`pending`; the last outcome `sent` or `dropped` is that; a permanent error, or the 8th retryable
one, is `dead`. A retryable error waits before the next attempt: 1 minute, doubling, at most an
hour. An alert's email first waits up to EXPLANATION_WAIT for the model's explanation of the
alert, and goes as soon as it exists (ADR-0008: the email never depends on it).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from types import MappingProxyType

from serpsense.domain.enums import OutboxOutcome, OutboxStatus

MAX_RETRYABLE = 8
FIRST_WAIT, LONGEST_WAIT = timedelta(minutes=1), timedelta(hours=1)
EXPLANATION_WAIT = timedelta(minutes=2)
FINAL = MappingProxyType(
    {
        OutboxOutcome.SENT: OutboxStatus.SENT,
        OutboxOutcome.DROPPED: OutboxStatus.DROPPED,
        OutboxOutcome.PERMANENT_ERROR: OutboxStatus.DEAD,
    }
)


def status(outcomes: Sequence[OutboxOutcome]) -> OutboxStatus:
    """A message's status from its attempts' outcomes, oldest first."""
    if outcomes and outcomes[-1] in FINAL:
        return FINAL[outcomes[-1]]
    retried = sum(outcome is OutboxOutcome.RETRYABLE_ERROR for outcome in outcomes)
    return OutboxStatus.DEAD if retried >= MAX_RETRYABLE else OutboxStatus.PENDING


def next_attempt(at: datetime, retryable_errors: int) -> datetime:
    """When to try again after the given number of retryable errors (at least one)."""
    if retryable_errors < 1:
        raise ValueError("a retry follows a retryable error")
    wait: timedelta = FIRST_WAIT * (1 << (retryable_errors - 1))
    return at + min(wait, LONGEST_WAIT)


def alert_email_key(alert_id: uuid.UUID) -> str:
    """An alert's email is written once (data-model §8)."""
    return f"alert:{alert_id}:email"


def otp_email_key(otp_code_id: uuid.UUID) -> str:
    """A sign-in code's email is written once (data-model §8)."""
    return f"otp:{otp_code_id}"

"""An outbox email's status from its attempts, and when to try again."""

from datetime import UTC, datetime, timedelta

import pytest

from serpsense.domain.enums import OutboxOutcome, OutboxStatus
from serpsense.domain.outbox import MAX_RETRYABLE, next_attempt, status

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
Out, St = OutboxOutcome, OutboxStatus
RETRY = Out.RETRYABLE_ERROR


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([], St.PENDING),
        ([RETRY] * (MAX_RETRYABLE - 1), St.PENDING),
        ([RETRY] * MAX_RETRYABLE, St.DEAD),
        ([RETRY, Out.SENT], St.SENT),
        ([RETRY, Out.DROPPED], St.DROPPED),
        ([Out.PERMANENT_ERROR], St.DEAD),
    ],
)
def test_the_status_follows_from_the_attempts(
    outcomes: list[OutboxOutcome], expected: OutboxStatus
) -> None:
    assert status(outcomes) is expected


def test_a_retry_waits_longer_each_time_up_to_an_hour() -> None:
    waits = [next_attempt(AT, n) - AT for n in range(1, 9)]
    assert waits[:4] == [timedelta(minutes=m) for m in (1, 2, 4, 8)]
    assert waits[-1] == timedelta(hours=1)
    with pytest.raises(ValueError, match="retryable"):
        next_attempt(AT, 0)

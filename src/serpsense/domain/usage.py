"""Usage periods for budgets: UTC calendar days and months, shared by budget checks and reports."""

from datetime import UTC, datetime


def day_start(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("a usage period needs a timezone-aware time")
    return at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(at: datetime) -> datetime:
    return day_start(at).replace(day=1)


def searches_left(budget: int | None, *, default: int, used: int) -> int:
    """A user's searches left this month: their budget, or the default when none was set."""
    return (default if budget is None else budget) - used

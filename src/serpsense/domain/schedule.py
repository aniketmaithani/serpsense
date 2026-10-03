"""Schedule slots and quiet hours (data-model §2 `brand_schedule_versions`, BUILD_PLAN §10).

Slots are counted in elapsed time from midnight in the brand's own timezone, so a 6-hour
schedule in India runs at 00:00, 06:00, 12:00 and 18:00 IST. On a day the clocks change, the
slots after the change sit an hour off their usual local times, and the day the clocks go back
ends with a short extra slot; no slot is skipped and none starts after the time asked for. A
slot's start is what `scans.scheduled_for` holds, which makes the dispatcher idempotent: every
run inside a slot computes the same value.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INTERVALS_MINUTES = frozenset({60, 180, 360, 720, 1440})


class InvalidSchedule(ValueError):
    """A schedule the dispatcher can't run (bad interval, quiet hours or timezone)."""


@dataclass(frozen=True)
class Schedule:
    interval_minutes: int
    timezone: str = "Asia/Kolkata"
    quiet_start: time | None = None  # local times; start after end means overnight
    quiet_end: time | None = None

    def __post_init__(self) -> None:
        if self.interval_minutes not in INTERVALS_MINUTES:
            raise InvalidSchedule("interval must be 1h, 3h, 6h, 12h or 24h")
        if (self.quiet_start is None) != (self.quiet_end is None) or (
            self.quiet_start is not None and self.quiet_start == self.quiet_end
        ):
            raise InvalidSchedule("quiet hours need a different start and end")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:  # "America" is a directory
            raise InvalidSchedule("unknown timezone") from exc

    def slot(self, at: datetime) -> datetime:
        """The start of the slot containing `at`, in UTC."""
        instant = _aware(at).astimezone(UTC)
        local = instant.astimezone(ZoneInfo(self.timezone))
        # The day's first instant: fold=0 picks the earlier of a repeated midnight.
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
        start = midnight.astimezone(UTC)
        interval = timedelta(minutes=self.interval_minutes)
        return start + (instant - start) // interval * interval

    def is_quiet(self, at: datetime) -> bool:
        """True inside quiet hours (start inclusive, end exclusive), in the brand's local time."""
        if self.quiet_start is None or self.quiet_end is None:
            return False
        now = _aware(at).astimezone(ZoneInfo(self.timezone)).time()
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= now < self.quiet_end
        return now >= self.quiet_start or now < self.quiet_end  # overnight


def _aware(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("a schedule needs a timezone-aware time")
    return at

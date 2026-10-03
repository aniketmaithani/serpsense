"""Port for the current time, so anything that depends on "now" can be tested (AGENTS §3)."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware in UTC."""
        ...


class SettableClock(Clock, Protocol):
    """A clock that can be set back, for replaying recorded scans at the times they ran."""

    def set(self, at: datetime) -> None:
        """From now on, the time reads from `at`."""
        ...

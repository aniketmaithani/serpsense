"""Port for the current time, so anything that depends on "now" can be tested (AGENTS §3)."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware in UTC."""
        ...

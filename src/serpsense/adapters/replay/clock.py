"""The replay clock: set to a recorded scan's time, each reading a millisecond after the last, so
a replayed scan's events keep their order at the time the scan ran (services/replay.py)."""

from datetime import UTC, datetime, timedelta
from threading import Lock

TICK = timedelta(milliseconds=1)


class ReplayClock:
    def __init__(self, at: datetime = datetime(2000, 1, 1, tzinfo=UTC)) -> None:
        self._at, self._lock = at, Lock()  # read from the collectors' threads too

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("a replay clock is set to an aware time")
        with self._lock:
            self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        with self._lock:
            self._at += TICK
            return self._at

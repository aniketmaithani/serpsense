"""Port for counting requests in a window (ADR-0004: Redis holds only rebuildable data).

Limits that protect a single account live in Postgres; this one guards against a single source
sending too much (ADR-0009: per IP). A limiter that can't reach its store lets the request
through and says so in the logs, so an outage of the cache never locks everyone out.
"""

from datetime import timedelta
from typing import Protocol


class RateLimiter(Protocol):
    def allow(self, key: str, *, limit: int, window: timedelta) -> bool:
        """Count one more request under `key`; False once there were `limit` in this window."""
        ...

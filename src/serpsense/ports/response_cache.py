"""Port for the local cache of SerpApi responses (ADR-0004: Redis holds rebuildable data only).

Keys are a request's `params_hash`; values are redacted payloads. The cache is best-effort:
when it is unavailable, `get` finds nothing and `set` does nothing, so searches still work.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Protocol


class ResponseCache(Protocol):
    def get(self, key: str) -> Mapping[str, Any] | None: ...

    def set(self, key: str, payload: Mapping[str, Any], *, ttl: timedelta) -> None:
        """Keep a payload for `ttl`; a TTL under a second writes nothing."""
        ...

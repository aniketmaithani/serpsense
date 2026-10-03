"""A response cache that keeps nothing, for replay mode: every search reaches the recording, so
each replayed scan gets its own recorded answer instead of the last one cached."""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any


class NullResponseCache:
    def get(self, key: str) -> Mapping[str, Any] | None:
        return None

    def set(self, key: str, payload: Mapping[str, Any], *, ttl: timedelta) -> None:
        return None

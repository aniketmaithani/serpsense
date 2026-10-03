"""SerpApi responses cached in Redis, best-effort (ADR-0004)."""

import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, cast

from redis import Redis
from redis.exceptions import RedisError

from serpsense.observability import get_logger

log = get_logger(__name__)

# Versioned, so a change to the payload shape can't read old entries.
PREFIX = "serpsense:serp:v1:"


class RedisResponseCache:
    def __init__(self, url: str) -> None:
        self._client: Redis = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    def get(self, key: str) -> Mapping[str, Any] | None:
        try:
            # redis-py types every command as possibly awaitable; this client is synchronous.
            raw = cast(bytes | None, self._client.get(PREFIX + key))
        except RedisError as exc:
            log.warning("response_cache.read_failed", error_type=type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if not isinstance(value, dict):
            log.warning("response_cache.entry_discarded")
            return None
        return value

    def set(self, key: str, payload: Mapping[str, Any], *, ttl: timedelta) -> None:
        """A TTL under a second means "don't cache": nothing is written."""
        seconds = int(ttl.total_seconds())
        if seconds < 1:
            return
        try:
            self._client.set(PREFIX + key, json.dumps(payload, ensure_ascii=False), ex=seconds)
        except RedisError as exc:
            log.warning("response_cache.write_failed", error_type=type(exc).__name__)

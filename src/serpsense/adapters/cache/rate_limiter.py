"""A fixed-window rate limiter on Redis (ADR-0004, ADR-0009).

Each key counts in a window that starts with its first request and expires with it (EXPIRE NX:
Redis 7 or later). Keys are an HMAC under a key of their own, so an IP address never sits in Redis
as itself and can't be recovered by hashing every address. Only a connection that can't be made
lets requests through; any other Redis error is raised.
"""

import hashlib
import hmac
from datetime import timedelta
from typing import cast

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from serpsense.observability import get_logger

log = get_logger(__name__)

PREFIX = "serpsense:limit:v1:"


class RedisRateLimiter:
    def __init__(self, url: str, key: bytes) -> None:
        self._client: Redis = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        self._key = key

    def allow(self, key: str, *, limit: int, window: timedelta) -> bool:
        name = PREFIX + hmac.new(self._key, key.encode(), hashlib.sha256).hexdigest()
        try:
            pipe = self._client.pipeline()
            pipe.incr(name)
            pipe.expire(name, int(window.total_seconds()), nx=True)
            count = cast(int, pipe.execute()[0])
        except (RedisConnectionError, RedisTimeoutError) as exc:  # let it through, and say so
            log.warning("rate_limit.unavailable", error_type=type(exc).__name__)
            return True
        return count <= limit

"""The Redis rate limiter on a real Redis: a window per key, and open when Redis is down."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from redis import Redis
from structlog.testing import capture_logs

from serpsense.adapters.cache.rate_limiter import PREFIX, RedisRateLimiter
from serpsense.ports.rate_limiter import RateLimiter

pytestmark = pytest.mark.integration

HOUR = timedelta(hours=1)
KEY = b"k" * 32


def test_a_key_gets_its_limit_per_window(redis_url: str) -> None:
    limiter: RateLimiter = RedisRateLimiter(redis_url, KEY)
    key = f"otp-request:{uuid.uuid4()}"
    assert [limiter.allow(key, limit=3, window=HOUR) for _ in range(4)] == [True] * 3 + [False]
    assert limiter.allow(f"{key}-other", limit=3, window=HOUR)  # keys count apart
    client = Redis.from_url(redis_url)
    names = [name.decode() for name in client.keys(PREFIX + "*")]
    assert names and all(key not in name for name in names)  # never the raw key ...
    assert PREFIX + hashlib.sha256(key.encode()).hexdigest() not in names  # ... nor a plain hash
    assert all(0 < client.ttl(name) <= 3600 for name in names)


def test_a_limiter_that_cant_reach_redis_lets_requests_through() -> None:
    limiter = RedisRateLimiter("redis://127.0.0.1:1/0", KEY)
    with capture_logs() as logs:
        assert limiter.allow("otp-request:x", limit=1, window=HOUR)
    assert "rate_limit.unavailable" in [entry["event"] for entry in logs]

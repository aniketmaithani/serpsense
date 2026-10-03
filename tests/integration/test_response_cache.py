"""The Redis response cache on a real Redis (ADR-0004)."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from redis import Redis
from testcontainers.redis import RedisContainer

from serpsense.adapters.cache.response_cache import PREFIX, RedisResponseCache
from serpsense.ports.response_cache import ResponseCache

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


def test_a_payload_round_trips_with_its_ttl(redis_url: str) -> None:
    cache: ResponseCache = RedisResponseCache(redis_url)  # mypy checks it satisfies the port
    payload = {"news_results": [{"title": "ओला fares"}]}
    cache.set("abc", payload, ttl=timedelta(hours=3))
    assert cache.get("abc") == payload
    assert 3 * 3600 - 5 <= Redis.from_url(redis_url).ttl(PREFIX + "abc") <= 3 * 3600
    assert cache.get("missing") is None


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]"])
def test_an_unusable_entry_is_a_miss(redis_url: str, raw: bytes) -> None:
    Redis.from_url(redis_url).set(PREFIX + "bad", raw)
    assert RedisResponseCache(redis_url).get("bad") is None


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-5), timedelta(milliseconds=900)])
def test_a_ttl_under_a_second_writes_nothing(redis_url: str, ttl: timedelta) -> None:
    cache = RedisResponseCache(redis_url)
    cache.set("brief", {"a": 1}, ttl=ttl)
    assert cache.get("brief") is None


def test_an_unreachable_redis_is_a_miss_not_an_error() -> None:
    cache = RedisResponseCache("redis://127.0.0.1:1/0")
    cache.set("abc", {"a": 1}, ttl=timedelta(minutes=1))
    assert cache.get("abc") is None

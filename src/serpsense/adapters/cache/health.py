"""Redis reachability check."""

from redis import Redis
from redis.exceptions import RedisError

from serpsense.observability import get_logger

log = get_logger(__name__)


class RedisHealthCheck:
    name = "redis"

    def __init__(self, url: str) -> None:
        self._client: Redis = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    def check(self) -> bool:
        try:
            return bool(self._client.ping())
        except RedisError as exc:
            log.warning("health.check_failed", dependency=self.name, error_type=type(exc).__name__)
            return False

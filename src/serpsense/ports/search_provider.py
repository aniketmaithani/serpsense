"""Port for SerpApi searches (ADR-0007).

One `search` call is exactly one HTTP request: retries, caching, budgets, the circuit breaker
and the ledger live in the search service, so every attempt is recorded. The adapter redacts
each payload before returning it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from serpsense.domain.enums import SerpEngine, SerpErrorCode, ServedFrom
from serpsense.domain.search import ParamValue, canonical_params, params_hash


@dataclass(frozen=True)
class SearchRequest:
    engine: SerpEngine
    # Validated on construction and kept in canonical form (strings), so equal requests compare
    # equal; never `engine`, `api_key` or `no_cache`.
    params: Mapping[str, ParamValue]
    # Skip SerpApi's own one-hour cache (cached results don't count against the quota).
    no_cache: bool = False

    def __post_init__(self) -> None:
        # Refuses what the ledger would reject; a private copy, so the caller can't change it.
        object.__setattr__(self, "params", canonical_params(self.params))

    @property
    def params_hash(self) -> str:
        return params_hash(self.engine, self.params)

    def __hash__(self) -> int:
        return hash((self.params_hash, self.no_cache))


@dataclass(frozen=True)
class SearchResponse:
    payload: Mapping[str, Any]  # redacted JSON object
    served_from: ServedFrom  # `serpapi_cache` or `live`; `local_cache` is the service's
    http_status: int
    latency_ms: int

    def __post_init__(self) -> None:
        if self.served_from is ServedFrom.LOCAL_CACHE:
            raise ValueError("a provider response is never served from the local cache")


class SearchFailed(Exception):
    """A classified failure. Its text is only the machine code: provider messages can hold
    the request URL, and with it the key."""

    def __init__(self, code: SerpErrorCode, *, http_status: int | None, latency_ms: int) -> None:
        if (http_status is None) != (code in {SerpErrorCode.NETWORK, SerpErrorCode.TIMEOUT}):
            raise ValueError("only network errors and timeouts have no HTTP status")
        super().__init__(code.value)
        self.code = code
        self.http_status = http_status
        self.latency_ms = latency_ms

    @property
    def transient(self) -> bool:
        """Retried, and counted by the circuit breaker: network, timeout, 429 and 5xx."""
        return self.code.is_transient

    def __reduce__(self) -> tuple[Any, ...]:
        return (_search_failed, (self.code, self.http_status, self.latency_ms))


def _search_failed(code: SerpErrorCode, http_status: int | None, latency_ms: int) -> SearchFailed:
    return SearchFailed(code, http_status=http_status, latency_ms=latency_ms)


class SearchProvider(Protocol):
    def search(self, request: SearchRequest) -> SearchResponse:
        """Make one request. Raises SearchFailed; never returns an unredacted payload."""
        ...

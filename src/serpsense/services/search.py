"""The search service: every SerpApi request goes through it (ADR-0007, data-model §5).

Local cache → the call, retrying transient failures with backoff and jitter. Every attempt is a
`serp_calls` row written in its own transaction; a successful call's redacted payload is kept
and cached.
"""

import random
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from serpsense.domain.enums import SerpCallOutcome, SerpEngine, SerpErrorCode, ServedFrom
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.response_cache import ResponseCache
from serpsense.ports.search_ledger import CallRecord, SearchLedger
from serpsense.ports.search_provider import SearchFailed, SearchProvider, SearchRequest

log = get_logger(__name__)

# BUILD_PLAN §6.2 defaults: fast-moving surfaces expire sooner.
DEFAULT_CACHE_TTL: Mapping[SerpEngine, timedelta] = MappingProxyType(
    {
        SerpEngine.GOOGLE: timedelta(hours=6),
        SerpEngine.GOOGLE_AI_OVERVIEW: timedelta(hours=6),
        SerpEngine.GOOGLE_AUTOCOMPLETE: timedelta(hours=6),
        SerpEngine.GOOGLE_NEWS: timedelta(hours=3),
        SerpEngine.GOOGLE_TRENDS: timedelta(hours=24),
        SerpEngine.GOOGLE_PLAY_PRODUCT: timedelta(hours=12),
        SerpEngine.GOOGLE_MAPS: timedelta(hours=12),
        SerpEngine.GOOGLE_MAPS_REVIEWS: timedelta(hours=12),
        SerpEngine.YOUTUBE: timedelta(hours=12),
    }
)


@dataclass(frozen=True)
class SearchLimits:
    attempts: int = 3
    backoff_seconds: float = 1.0  # doubled per retry, capped, plus up to as much jitter
    backoff_cap_seconds: float = 8.0
    cache_ttl: Mapping[SerpEngine, timedelta] = field(default_factory=lambda: DEFAULT_CACHE_TTL)

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("at least one attempt")
        if set(SerpEngine) - set(self.cache_ttl):
            raise ValueError("every engine needs a cache TTL")


@dataclass(frozen=True)
class SearchPorts:
    provider: SearchProvider
    ledger: SearchLedger
    cache: ResponseCache
    clock: Clock


@dataclass(frozen=True)
class SearchResult:
    call_id: uuid.UUID
    payload: Mapping[str, Any]
    served_from: ServedFrom


class SearchService:
    def __init__(
        self,
        ports: SearchPorts,
        limits: SearchLimits,
        *,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._ports, self._limits = ports, limits
        self._sleep, self._jitter = sleep, jitter

    def search(
        self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
    ) -> SearchResult:
        """Raises SearchFailed when the call fails. If keeping the payload fails after a
        successful call, that error propagates: the call is already recorded, so nothing billed
        goes unrecorded, and a payload the ledger rejects is never used."""
        ports = self._ports
        call = _Call(ports, request, user_id, scan_id)
        if not request.no_cache:
            cached = ports.cache.get(request.params_hash)
            if cached is not None:
                call_id = call.record(SerpCallOutcome.SUCCEEDED, served_from=ServedFrom.LOCAL_CACHE)
                return SearchResult(call_id, cached, ServedFrom.LOCAL_CACHE)
        return self._attempt(call)

    def _attempt(self, call: "_Call") -> SearchResult:
        for attempt in range(1, self._limits.attempts + 1):
            try:
                response = self._ports.provider.search(call.request)
            except SearchFailed as failure:
                call.record(
                    SerpCallOutcome.FAILED,
                    http_status=failure.http_status,
                    error_code=failure.code,
                    latency_ms=failure.latency_ms,
                )
                log.warning("serp_call.failed", engine=call.request.engine, error_code=failure.code)
                if not failure.transient or attempt == self._limits.attempts:
                    raise
                self._sleep(self._backoff(attempt))
                continue
            call_id = call.record(
                SerpCallOutcome.SUCCEEDED,
                served_from=response.served_from,
                http_status=response.http_status,
                latency_ms=response.latency_ms,
            )
            self._ports.ledger.store_payload(call_id, response.payload, at=self._ports.clock.now())
            ttl = self._limits.cache_ttl[call.request.engine]
            self._ports.cache.set(call.request.params_hash, response.payload, ttl=ttl)
            return SearchResult(call_id, response.payload, response.served_from)
        raise AssertionError("the last attempt either returns or raises")

    def _backoff(self, attempt: int) -> float:
        doubled = self._limits.backoff_seconds * 2.0 ** (attempt - 1)
        base = min(self._limits.backoff_cap_seconds, doubled)
        return base + self._jitter() * self._limits.backoff_seconds


@dataclass(frozen=True)
class _Call:
    """One logical search, which may take several ledger rows."""

    ports: SearchPorts
    request: SearchRequest
    user_id: uuid.UUID
    scan_id: uuid.UUID | None

    def record(
        self,
        outcome: SerpCallOutcome,
        *,
        served_from: ServedFrom | None = None,
        http_status: int | None = None,
        error_code: SerpErrorCode | None = None,
        latency_ms: int = 0,
    ) -> uuid.UUID:
        record = CallRecord(
            user_id=self.user_id,
            scan_id=self.scan_id,
            request=self.request,
            outcome=outcome,
            served_from=served_from,
            http_status=http_status,
            error_code=error_code,
            latency_ms=latency_ms,
            created_at=self.ports.clock.now(),
        )
        return self.ports.ledger.record_call(record)

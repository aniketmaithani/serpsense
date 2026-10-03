"""SerpApi SDK adapter (ADR-0007): one HTTP request per call, classified errors, and a payload
that is redacted before it leaves this module.

The SDK writes `api_key` into the params dict it is given and puts the request URL (with the
key) into its error messages, so it always gets a throwaway copy, and no SDK error is chained
to the error raised here (AGENTS §3).
"""

import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import serpapi
from serpapi import exceptions as sdk_errors

from serpsense.adapters.serp.redaction import redact
from serpsense.domain.enums import SerpErrorCode, ServedFrom
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.search_provider import SearchFailed, SearchRequest, SearchResponse

log = get_logger(__name__)

TIMEOUT_SECONDS = 30.0
# A cached search comes back with the original search's metadata, so it was created earlier.
CACHED_IF_OLDER_THAN = timedelta(seconds=60)


class SdkClient(Protocol):
    def search(self, params: dict[str, Any]) -> Any: ...


class SerpApiSearchProvider:
    def __init__(
        self,
        *,
        api_key: str,
        clock: Clock,
        sdk: SdkClient | None = None,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("a SerpApi key is required")
        self._api_key = api_key
        self._clock = clock
        self._sdk = sdk
        self._timer = timer
        # The SDK keeps one requests.Session per client, which isn't safe to share between
        # the scan's worker threads.
        self._local = threading.local()

    def search(self, request: SearchRequest) -> SearchResponse:
        params: dict[str, Any] = {**request.params, "engine": request.engine.value}
        if request.no_cache:
            params["no_cache"] = "true"
        started_at, started = self._clock.now(), self._timer()
        failure: SearchFailed | None = None
        try:
            results = self._client().search(dict(params))
        # Every exception at this third-party boundary: the SDK also breaks in its own ways (a
        # JSON scalar body breaks its UserDict; an odd or deeply nested error body breaks its
        # HTTPError), and an unclassified one would carry the request URL and key with it.
        except Exception as exc:  # noqa: BLE001  # classified, then raised outside the handler
            failure = _classified(exc, _elapsed_ms(self._timer, started))
        if failure is not None:
            raise failure  # outside the handler, so the SDK error (and its URL) isn't chained
        latency_ms = _elapsed_ms(self._timer, started)
        payload = self._redacted(results, latency_ms)
        metadata = payload.get("search_metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        if metadata.get("status") == "Error":
            raise SearchFailed(SerpErrorCode.SEARCH_ERROR, http_status=200, latency_ms=latency_ms)
        served_from = (
            ServedFrom.LIVE
            if request.no_cache
            else _served_from(metadata.get("created_at"), started_at)
        )
        return SearchResponse(
            payload=payload, served_from=served_from, http_status=200, latency_ms=latency_ms
        )

    def _client(self) -> SdkClient:
        if self._sdk is not None:
            return self._sdk
        client: SdkClient | None = getattr(self._local, "client", None)
        if client is None:
            client = serpapi.Client(api_key=self._api_key, timeout=TIMEOUT_SECONDS)
            self._local.client = client
        return client

    def _redacted(self, results: Any, latency_ms: int) -> dict[str, Any]:
        """A redacted, non-empty JSON object; anything else is an invalid response."""
        payload: dict[str, Any] | None = None
        if isinstance(results, Mapping) and results:
            try:
                payload = redact(results, api_key=self._api_key)
            except (RecursionError, TypeError):  # a hostile depth, or keys that aren't strings
                payload = None
        if payload is None:
            raise SearchFailed(
                SerpErrorCode.INVALID_RESPONSE, http_status=200, latency_ms=latency_ms
            )
        return payload


def _elapsed_ms(timer: Callable[[], float], started: float) -> int:
    return max(0, round((timer() - started) * 1000))


def _classified(exc: BaseException, latency_ms: int) -> SearchFailed:
    """Map an SDK or transport error to a code; its message is never kept."""
    status = _status_of(exc)
    if isinstance(exc, sdk_errors.TimeoutError):
        code, status = SerpErrorCode.TIMEOUT, None
    elif isinstance(exc, sdk_errors.HTTPConnectionError):
        code, status = SerpErrorCode.NETWORK, None
    elif status == 429:
        code = SerpErrorCode.HTTP_429
    elif status is not None and status >= 500:
        code = SerpErrorCode.HTTP_5XX
    elif status is not None:
        code = SerpErrorCode.HTTP_4XX
    elif isinstance(exc, OSError):  # the rest of requests' transport errors
        code = SerpErrorCode.NETWORK
    else:  # the SDK broke on a body it received
        log.warning("serp_call.response_unusable", error_type=type(exc).__name__)
        code, status = SerpErrorCode.INVALID_RESPONSE, 200
    return SearchFailed(code, http_status=status, latency_ms=latency_ms)


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status, also when the SDK broke while wrapping the error response."""
    for candidate in (exc, getattr(exc.__context__, "response", None)):
        status = getattr(candidate, "status_code", None)
        if isinstance(status, int) and status >= 100:
            return status
    return None


def _served_from(created_at: object, started_at: datetime) -> ServedFrom:
    """`serpapi_cache` when SerpApi returned a search created over a minute before this request.

    Unknown counts as live. A local clock running more than a minute ahead of SerpApi's would
    label live calls as cached, so hosts keep NTP time; `no_cache` requests are always live.
    """
    if not isinstance(created_at, str):
        return ServedFrom.LIVE
    try:
        created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
    except ValueError:
        return ServedFrom.LIVE
    return (
        ServedFrom.SERPAPI_CACHE if created < started_at - CACHED_IF_OLDER_THAN else ServedFrom.LIVE
    )

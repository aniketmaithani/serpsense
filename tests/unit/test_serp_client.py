"""SerpApi SDK adapter: one request, a throwaway params dict, classified errors with no provider
text, served_from from the search metadata, and a redacted payload (ADR-0007)."""

import json
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from serpapi import exceptions as sdk_errors
from serpapi.http import requests  # the SDK's HTTP library, patched below; not a direct dependency
from structlog.testing import capture_logs

from serpsense.adapters.serp.client import TIMEOUT_SECONDS, SerpApiSearchProvider
from serpsense.domain.enums import SerpEngine, SerpErrorCode, ServedFrom
from serpsense.ports.search_provider import SearchFailed, SearchRequest

pytestmark = pytest.mark.unit

KEY = "e" * 64
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
REQUEST = SearchRequest(engine=SerpEngine.GOOGLE_NEWS, params={"q": "Ola", "gl": "in"})
FRESH = SearchRequest(engine=SerpEngine.GOOGLE_NEWS, params={"q": "Ola", "gl": "in"}, no_cache=True)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeSdk:
    """Behaves like serpapi.Client.search: it writes api_key into the dict it is given."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error
        self.calls: list[dict[str, Any]] = []

    def search(self, params: dict[str, Any]) -> Any:
        params["api_key"] = KEY
        self.calls.append(dict(params))
        if self.error is not None:
            raise self.error
        return self.result


def provider(sdk: FakeSdk) -> SerpApiSearchProvider:
    ticks = iter([10.0, 10.25])
    return SerpApiSearchProvider(
        api_key=KEY, clock=FixedClock(), sdk=sdk, timer=lambda: next(ticks)
    )


def payload(created_at: str = "2026-10-03 09:00:00 UTC", **extra: Any) -> dict[str, Any]:
    return {"search_metadata": {"id": "s1", "status": "Success", "created_at": created_at}, **extra}


def http_error(status: int) -> Exception:
    response = requests.Response()
    response.status_code = status
    response._content = b'{"error": "nope"}'
    url = f"https://serpapi.com/search?q=Ola&api_key={KEY}"
    return sdk_errors.HTTPError(requests.HTTPError(f"{status} for url: {url}", response=response))


def test_a_throwaway_params_dict_goes_out_and_a_redacted_payload_comes_back() -> None:
    sdk = FakeSdk(result=payload(search_parameters={"q": "Ola", "api_key": KEY}))
    response = provider(sdk).search(FRESH)
    assert sdk.calls == [
        {"q": "Ola", "gl": "in", "engine": "google_news", "no_cache": "true", "api_key": KEY}
    ]
    assert FRESH.params == {"q": "Ola", "gl": "in"}  # the SDK's api_key never lands here
    assert response.payload["search_parameters"] == {"q": "Ola"}
    assert (response.http_status, response.latency_ms) == (200, 250)


@pytest.mark.parametrize(
    ("request_", "result", "served_from"),
    [
        (REQUEST, payload("2026-10-03 09:00:00 UTC"), ServedFrom.LIVE),
        (REQUEST, payload("2026-10-03 08:59:30 UTC"), ServedFrom.LIVE),
        (REQUEST, payload("2026-10-03 08:58:59 UTC"), ServedFrom.SERPAPI_CACHE),  # over a minute
        (REQUEST, payload("2026-10-03 09:05:00 UTC"), ServedFrom.LIVE),  # SerpApi's clock ahead
        (REQUEST, payload("yesterday"), ServedFrom.LIVE),  # unknown counts as billable
        (REQUEST, {"organic_results": []}, ServedFrom.LIVE),
        (REQUEST, {"search_metadata": "odd", "organic_results": []}, ServedFrom.LIVE),
        (FRESH, payload("2026-10-03 08:00:00 UTC"), ServedFrom.LIVE),  # no_cache is always live
    ],
)
def test_served_from(request_: SearchRequest, result: Any, served_from: ServedFrom) -> None:
    assert provider(FakeSdk(result=result)).search(request_).served_from is served_from


@pytest.mark.parametrize(
    ("error", "code", "status"),
    [
        (sdk_errors.TimeoutError("read timed out"), SerpErrorCode.TIMEOUT, None),
        (
            sdk_errors.HTTPConnectionError(requests.ConnectionError("x")),
            SerpErrorCode.NETWORK,
            None,
        ),
        (requests.exceptions.ChunkedEncodingError("broken"), SerpErrorCode.NETWORK, None),
        (sdk_errors.HTTPError(ValueError("no response")), SerpErrorCode.NETWORK, None),  # status -1
        (http_error(429), SerpErrorCode.HTTP_429, 429),
        (http_error(500), SerpErrorCode.HTTP_5XX, 500),
        (http_error(503), SerpErrorCode.HTTP_5XX, 503),
        (http_error(401), SerpErrorCode.HTTP_4XX, 401),
        (http_error(400), SerpErrorCode.HTTP_4XX, 400),
    ],
)
def test_errors_are_classified_without_provider_text(
    error: Exception, code: SerpErrorCode, status: int | None
) -> None:
    with pytest.raises(SearchFailed) as exc:
        provider(FakeSdk(error=error)).search(REQUEST)
    failure = exc.value
    assert (failure.code, failure.http_status, failure.latency_ms) == (code, status, 250)
    assert str(failure) == code and KEY not in repr(failure.args)
    assert failure.__cause__ is None and failure.__context__ is None


def deeply_nested(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {}
    for _ in range(depth):
        node = {"a": node}
    return node


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (payload() | {"search_metadata": {"status": "Error"}}, SerpErrorCode.SEARCH_ERROR),
        ("<html></html>", SerpErrorCode.INVALID_RESPONSE),
        ({}, SerpErrorCode.INVALID_RESPONSE),
        (deeply_nested(5000), SerpErrorCode.INVALID_RESPONSE),  # redaction can't recurse that deep
    ],
)
def test_unusable_responses_fail_permanently(result: Any, code: SerpErrorCode) -> None:
    with pytest.raises(SearchFailed) as exc:
        provider(FakeSdk(result=result)).search(REQUEST)
    assert (exc.value.code, exc.value.transient, exc.value.http_status) == (code, False, 200)


def test_no_results_is_not_an_error() -> None:
    """SerpApi reports an empty result page with an `error` text but a successful status."""
    result = payload(error="Google hasn't returned any results for this query.")
    assert provider(FakeSdk(result=result)).search(REQUEST).payload["error"].startswith("Google")


def test_a_key_is_required() -> None:
    with pytest.raises(ValueError, match="key"):
        SerpApiSearchProvider(api_key="", clock=FixedClock())


class Transport:
    """Stands in for requests.Session.request under the real SDK."""

    def __init__(self, body: bytes, error: Exception | None = None, status: int = 200) -> None:
        self.body, self.error, self.status = body, error, status
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> requests.Response:  # set on the class: not bound
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        response.headers["Content-Type"] = "application/json"
        response._content = self.body
        return response


DEEP = b"[" * 100_000 + b"]" * 100_000


def real_sdk_provider() -> SerpApiSearchProvider:
    return SerpApiSearchProvider(api_key=KEY, clock=FixedClock())


def test_the_real_sdk_gets_the_timeout_and_no_cache_only_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = Transport(json.dumps(payload(organic_results=[])).encode())
    monkeypatch.setattr(requests.Session, "request", transport)
    assert real_sdk_provider().search(REQUEST).payload["organic_results"] == []
    sent = transport.calls[0]
    assert sent["timeout"] == TIMEOUT_SECONDS == 30.0
    assert sent["params"] == {"q": "Ola", "gl": "in", "engine": "google_news", "api_key": KEY}
    real_sdk_provider().search(FRESH)
    assert transport.calls[1]["params"]["no_cache"] == "true"


@pytest.mark.parametrize(
    ("transport", "code", "status"),
    [
        (Transport(b"5"), SerpErrorCode.INVALID_RESPONSE, 200),  # a JSON scalar breaks UserDict
        (Transport(b"null"), SerpErrorCode.INVALID_RESPONSE, 200),  # an empty result object
        (Transport(b"[[1, 2]]"), SerpErrorCode.INVALID_RESPONSE, 200),  # keys that aren't strings
        (Transport(DEEP), SerpErrorCode.INVALID_RESPONSE, 200),
        (Transport(b"", requests.ConnectionError(f"x?api_key={KEY}")), SerpErrorCode.NETWORK, None),
        # The SDK's HTTPError calls .get on the error body, so a non-object body breaks it.
        (Transport(b"[]", status=503), SerpErrorCode.HTTP_5XX, 503),
        (Transport(b"null", status=429), SerpErrorCode.HTTP_429, 429),
        (Transport(b'"nope"', status=401), SerpErrorCode.HTTP_4XX, 401),
        (Transport(DEEP, status=503), SerpErrorCode.HTTP_5XX, 503),  # too deep to parse
    ],
)
def test_the_real_sdk_failures_are_classified_and_unchained(
    monkeypatch: pytest.MonkeyPatch, transport: Transport, code: SerpErrorCode, status: int | None
) -> None:
    monkeypatch.setattr(requests.Session, "request", transport)
    with pytest.raises(SearchFailed) as exc:
        real_sdk_provider().search(REQUEST)
    assert (exc.value.code, exc.value.http_status) == (code, status)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_each_thread_gets_its_own_sdk_client() -> None:
    provider_ = real_sdk_provider()
    clients: list[object] = []
    worker = threading.Thread(target=lambda: clients.append(provider_._client()))
    worker.start()
    worker.join()
    assert provider_._client() is provider_._client()
    assert clients[0] is not provider_._client()


def test_a_broken_body_logs_only_the_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests.Session, "request", Transport(b"5"))
    with capture_logs() as logs, pytest.raises(SearchFailed):
        real_sdk_provider().search(REQUEST)
    assert logs == [
        {"event": "serp_call.response_unusable", "error_type": "TypeError", "log_level": "warning"}
    ]

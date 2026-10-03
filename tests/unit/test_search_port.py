"""The search port's value objects and failure type (ADR-0007)."""

import copy
import pickle
import re

import pytest

from serpsense.domain.enums import SerpEngine, SerpErrorCode, ServedFrom
from serpsense.domain.search import InvalidSearchParams, ParamValue, params_hash
from serpsense.ports.search_provider import SearchFailed, SearchRequest, SearchResponse

pytestmark = pytest.mark.unit


def test_request_keeps_a_canonical_private_copy() -> None:
    source: dict[str, ParamValue] = {"q": "Ola", "num": 10}
    request = SearchRequest(engine=SerpEngine.GOOGLE, params=source)
    source["q"] = "changed"
    assert request.params == {"q": "Ola", "num": "10"}
    assert request == SearchRequest(engine=SerpEngine.GOOGLE, params={"num": "10", "q": "Ola"})
    assert pickle.loads(pickle.dumps(request)) == request == copy.deepcopy(request)  # noqa: S301  # our own object


def test_requests_hash_by_their_canonical_params() -> None:
    first = SearchRequest(engine=SerpEngine.GOOGLE, params={"q": "Ola", "num": 10})
    second = SearchRequest(engine=SerpEngine.GOOGLE, params={"num": 10, "q": "Ola"})
    assert first == second and hash(first) == hash(second)
    assert first.params_hash == params_hash(SerpEngine.GOOGLE, {"num": "10", "q": "Ola"})


def test_request_refuses_what_the_ledger_would_reject() -> None:
    with pytest.raises(InvalidSearchParams):
        SearchRequest(engine=SerpEngine.GOOGLE, params={"q": "Ola", "no_cache": "true"})


def test_response_is_never_from_the_local_cache() -> None:
    with pytest.raises(ValueError, match="local cache"):
        SearchResponse(
            payload={}, served_from=ServedFrom.LOCAL_CACHE, http_status=200, latency_ms=1
        )


@pytest.mark.parametrize(
    ("code", "status", "transient"),
    [
        (SerpErrorCode.NETWORK, None, True),
        (SerpErrorCode.TIMEOUT, None, True),
        (SerpErrorCode.HTTP_429, 429, True),
        (SerpErrorCode.HTTP_5XX, 503, True),
        (SerpErrorCode.HTTP_4XX, 400, False),
        (SerpErrorCode.SEARCH_ERROR, 200, False),
        (SerpErrorCode.INVALID_RESPONSE, 200, False),
    ],
)
def test_failure_text_is_the_code_and_transience_follows_it(
    code: SerpErrorCode, status: int | None, transient: bool
) -> None:
    failure = SearchFailed(code, http_status=status, latency_ms=12)
    assert (str(failure), failure.transient, failure.latency_ms) == (code.value, transient, 12)


@pytest.mark.parametrize(
    ("code", "status"), [(SerpErrorCode.NETWORK, 500), (SerpErrorCode.HTTP_429, None)]
)
def test_failure_code_and_status_must_agree(code: SerpErrorCode, status: int | None) -> None:
    with pytest.raises(ValueError, match="HTTP status"):
        SearchFailed(code, http_status=status, latency_ms=1)


def test_failure_survives_copy_and_pickle() -> None:
    failure = SearchFailed(SerpErrorCode.HTTP_4XX, http_status=401, latency_ms=80)
    pickled = pickle.loads(pickle.dumps(failure))  # noqa: S301  # round-trips our own object
    for clone in (copy.copy(failure), pickled):
        assert (clone.code, clone.http_status, clone.latency_ms) == (
            SerpErrorCode.HTTP_4XX,
            401,
            80,
        )


def test_error_codes_fit_the_ledger_format() -> None:
    """Every code passes ck_serp_calls_error_code_format."""
    assert all(re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", code.value) for code in SerpErrorCode)

"""Canonical SerpApi params: the ledger's hash input and the no-key rule (data-model §5)."""

import hashlib
from typing import Any

import pytest

from serpsense.domain.enums import SerpEngine
from serpsense.domain.search import (
    InvalidSearchParams,
    ParamValue,
    canonical_json,
    contains_api_key,
    params_hash,
)

pytestmark = pytest.mark.unit


def test_canonical_json_sorts_keys_and_adds_the_engine() -> None:
    params: dict[str, ParamValue] = {"q": "ola", "hl": "en", "gl": "in", "num": 10}
    assert canonical_json(SerpEngine.GOOGLE_NEWS, params) == (
        '{"engine":"google_news","gl":"in","hl":"en","num":"10","q":"ola"}'
    )


def test_an_integer_and_its_string_are_the_same_request() -> None:
    assert params_hash(SerpEngine.GOOGLE, {"num": 10}) == params_hash(
        SerpEngine.GOOGLE, {"num": "10"}
    )


def test_hash_ignores_key_order_but_not_the_engine() -> None:
    first = params_hash(SerpEngine.GOOGLE, {"q": "ola", "gl": "in"})
    assert first == params_hash(SerpEngine.GOOGLE, {"gl": "in", "q": "ola"})
    assert first != params_hash(SerpEngine.GOOGLE_NEWS, {"q": "ola", "gl": "in"})


def test_hash_is_sha256_of_the_utf8_canonical_json() -> None:
    expected = hashlib.sha256(b'{"engine":"google_news","q":"voltbox"}').hexdigest()
    assert params_hash(SerpEngine.GOOGLE_NEWS, {"q": "voltbox"}) == expected


def test_non_ascii_text_is_not_escaped() -> None:
    """A Hindi query hashes the same in every runtime because it is kept as UTF-8."""
    assert (
        canonical_json(SerpEngine.GOOGLE_NEWS, {"q": "ओला"}) == '{"engine":"google_news","q":"ओला"}'
    )


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"engine": "google"}, "reserved"),
        ({"api_key": "k"}, "reserved"),
        ({"no_cache": "true"}, "reserved"),
        ({"async": "true"}, "reserved"),
        ({"output": "html"}, "reserved"),
        ({"q": "ola", "next": "https://serpapi.com/search?api_key=k"}, "API key"),
        ({"safe": True}, "strings or integers"),
        ({"start": 1.5}, "strings or integers"),
        ({"num": 10**5000}, "strings or integers"),
        ({1: "x"}, "names must be strings"),
        ({"q": "ola\x00"}, "control"),
        ({"q": "ola\tola"}, "control"),
        ({"q": "\x1api_key=x"}, "control"),
        ({"q": "ola\ud800"}, "UTF-8"),
    ],
)
def test_unsafe_params_are_refused(params: dict[str, Any], reason: str) -> None:
    with pytest.raises(InvalidSearchParams, match=reason):
        canonical_json(SerpEngine.GOOGLE, params)


def test_refusal_never_echoes_values() -> None:
    with pytest.raises(InvalidSearchParams) as exc:
        canonical_json(SerpEngine.GOOGLE, {"q": "https://serpapi.com/search?api_key=secret"})
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"q": "ola api_key leak"}, False),  # words in user text are not the key
        ({"q": "ola", "items": ["api_key", 3, None]}, False),
        ({"api_key": None}, True),
        ({"a": [{"b": {"api_key": "k"}}]}, True),
        ({"next": "https://serpapi.com/x?api_key=k"}, True),
        ({"q": "\x1api_key=x"}, True),  # Postgres writes chr(26) as \u001a: "\u001api_key="
        ({"https://serpapi.com/x?api_key=k": 1}, True),
        ([[{"api_key": 1}]], True),
        ("plain", False),
    ],
)
def test_contains_api_key(value: object, expected: bool) -> None:
    assert contains_api_key(value) is expected

"""Canonical SerpApi request parameters (data-model §5, ADR-0007).

The ledger identifies a request by `params_hash`, and `contains_api_key` mirrors the database
CHECK `ddl.no_api_key_check`, so a request the ledger would reject is refused before SerpApi
bills it.
"""

import hashlib
import json
from collections.abc import Mapping

from serpsense.domain.enums import SerpEngine

# Booleans are passed as "true"/"false": the SDK would send Python's "True".
ParamValue = str | int
# `no_cache` is SearchRequest.no_cache; `async` is billed but returns no results; `output`
# other than JSON can't be parsed.
RESERVED_PARAMS = frozenset({"api_key", "async", "engine", "no_cache", "output"})


class InvalidSearchParams(ValueError):
    """Parameters the ledger would reject; raised before any call is made."""


def canonical_params(params: Mapping[str, ParamValue]) -> dict[str, str]:
    """Validated params with integers written as strings (`num=10` is the HTTP request "10")."""
    if not all(isinstance(key, str) for key in params):
        raise InvalidSearchParams("parameter names must be strings")
    reserved = sorted(RESERVED_PARAMS & params.keys())
    if reserved:
        raise InvalidSearchParams(f"reserved parameters: {', '.join(reserved)}")
    if any(
        isinstance(value, bool) or not isinstance(value, str | int) for value in params.values()
    ):
        raise InvalidSearchParams("parameter values must be strings or integers")
    try:
        document = {key: str(value) for key, value in params.items()}
    except ValueError:  # an integer too long to write out
        raise InvalidSearchParams("parameter values must be strings or integers") from None
    if not all(_plain_text(text) for text in [*document, *document.values()]):
        raise InvalidSearchParams("parameters must be UTF-8 text without control characters")
    if contains_api_key(document):
        raise InvalidSearchParams("parameters must not carry an API key")
    return document


def canonical_json(engine: SerpEngine, params: Mapping[str, ParamValue]) -> str:
    """`canonical_params` plus `engine`: sorted keys, compact separators, UTF-8 without ASCII
    escapes."""
    document = {**canonical_params(params), "engine": engine.value}
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def params_hash(engine: SerpEngine, params: Mapping[str, ParamValue]) -> str:
    return hashlib.sha256(canonical_json(engine, params).encode()).hexdigest()


def _plain_text(text: str) -> bool:
    """No control characters (jsonb rejects NUL) and no lone surrogates (not valid UTF-8)."""
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return not any(ord(char) < 0x20 for char in text)


def contains_api_key(value: object) -> bool:
    """An `api_key` field at any depth, or `api_key=` in the JSON text of any key or string.

    Strings are checked as JSON writes them, because the CHECK reads the jsonb text: Postgres
    writes chr(26) as \\u001a, so chr(26) + "pi_key=" reads as "api_key=" there.
    """
    if isinstance(value, Mapping):
        return any(
            key == "api_key" or contains_api_key(key) or contains_api_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(contains_api_key(item) for item in value)
    return isinstance(value, str) and "api_key=" in json.dumps(value, ensure_ascii=False)

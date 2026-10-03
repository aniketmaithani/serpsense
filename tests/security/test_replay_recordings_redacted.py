"""The replay recordings the package ships hold only what the parsers read: no key, nothing
key-shaped, no reviewer or creator, no link to a review, no tracking parameter on a link
(AGENTS §6). Every file is checked as it is committed."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from serpsense.adapters.replay.recording import (
    ANY_HOST,
    LINK_PARAMS,
    RECORDINGS,
    REVIEW_ID,
    Recording,
    cleaned,
)
from serpsense.adapters.serp.redaction import IDENTITY_FIELDS, REVIEWER_FIELDS
from serpsense.services.demo import DEMO_BRANDS

pytestmark = pytest.mark.security

FILES = sorted(RECORDINGS.glob("*.json"))
NEVER = IDENTITY_FIELDS | REVIEWER_FIELDS | {"avatar", "thumbnail", "favicon", "serpapi_link"}


def keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


def links(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "link" and isinstance(item, str):
                yield item
            yield from links(item)
    elif isinstance(value, list):
        for item in value:
            yield from links(item)


def test_the_demo_brands_are_recorded() -> None:
    assert {path.stem for path in FILES} == {demo.slug for demo in DEMO_BRANDS}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_a_recording_holds_only_what_the_parsers_read(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "api_key" not in text and not re.search(r"\b[0-9a-fA-F]{64}\b", text)
    recording = Recording.model_validate_json(text)  # refuses anything key-shaped too
    assert recording.brand == path.stem
    engines = {a.payload: a.engine for scan in recording.scans for a in scan.answers}
    assert set(engines) == set(range(len(recording.payloads)))  # no payload kept unasked
    for index, payload in enumerate(recording.payloads):
        assert cleaned(engines[index], payload) == payload  # nothing the parser doesn't read
        assert not (NEVER - {"title", "link"}) & set(keys(payload))
        reviews = payload.get("reviews", [])
        assert all(REVIEW_ID.match(review["id"]) for review in reviews)  # no link to a review
        assert all(set(review) <= {"id", "snippet", "rating", "iso_date"} for review in reviews)
        for link in links(payload):  # no tracking, sharing or language parameter
            parts = urlsplit(link)
            names = {name for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
            assert names <= LINK_PARAMS.get(parts.netloc.lower(), ANY_HOST), link
            assert not parts.fragment or parts.fragment.startswith(("/", "!")), link

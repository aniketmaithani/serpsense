"""The demo story replay mode plays holds only what the parsers read and nothing real: no key,
nothing key-shaped, no reviewer or creator, no link to a review, no tracking parameter, and every
link on example.com, so it names no real site, company or person (AGENTS §6)."""

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from serpsense.adapters.replay.recording import (
    ANY_HOST,
    LINK_PARAMS,
    REVIEW_ID,
    Recording,
    cleaned,
    dump,
)
from serpsense.adapters.replay.story.build import BRANDS, recordings
from serpsense.adapters.serp.redaction import IDENTITY_FIELDS, REVIEWER_FIELDS

pytestmark = pytest.mark.security

RECORDINGS = recordings()
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


def test_every_brand_of_the_story_is_recorded() -> None:
    assert [r.brand for r in RECORDINGS] == [brand.slug for brand in BRANDS]


@pytest.mark.parametrize("recording", RECORDINGS, ids=lambda r: r.brand)
def test_a_recording_holds_only_what_the_parsers_read(recording: Recording) -> None:
    text = dump(recording)
    assert "api_key" not in text and not re.search(r"\b[0-9a-fA-F]{64}\b", text)
    assert Recording.model_validate_json(text) == recording  # refuses anything key-shaped too
    engines = {a.payload: a.engine for scan in recording.scans for a in scan.answers}
    assert set(engines) == set(range(len(recording.payloads)))  # no payload kept unasked
    for index, payload in enumerate(recording.payloads):
        assert cleaned(engines[index], payload) == payload  # nothing the parser doesn't read
        assert not (NEVER - {"title", "link"}) & set(keys(payload))
        reviews = payload.get("reviews", [])
        assert all(REVIEW_ID.match(review["id"]) for review in reviews)  # no link to a review
        assert all(set(review) <= {"id", "snippet", "rating", "iso_date"} for review in reviews)
        for link in links(payload):  # fictional, with no tracking or sharing parameter
            parts = urlsplit(link)
            assert parts.scheme == "https" and parts.netloc.endswith("example.com"), link
            names = {name for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
            assert names <= LINK_PARAMS.get(parts.netloc.lower(), ANY_HOST), link

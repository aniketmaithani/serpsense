"""Committed SerpApi fixtures never hold a key or author identity (ADR-0007, AGENTS §6)."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.security

FIXTURES = sorted((Path(__file__).parents[1] / "fixtures" / "serpapi").rglob("*.json"))
IDENTITY = {
    "author",
    "authors",
    "avatar",
    "channel",
    "channel_results",
    "contributor_id",
    "developer_contact",
    "profile_name",
    "short_videos",
    "user",
    "username",
}


def keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


def test_there_are_fixtures_to_check() -> None:
    assert FIXTURES


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_is_redacted(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "api_key" not in text
    assert not re.search(r"\b[0-9a-f]{64}\b", text)  # nothing shaped like a SerpApi key
    payload = json.loads(text)
    assert not IDENTITY & set(keys(payload))
    reviews = payload.get("reviews", [])
    assert not [r for r in reviews if "response" in r]  # replies greet reviewers by name
    metadata = payload.get("search_metadata", {})
    assert not [v for v in metadata.values() if isinstance(v, str) and v.startswith("http")]

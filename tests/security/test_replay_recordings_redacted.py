"""The replay recordings the package ships hold no key and no author or reviewer identity
(AGENTS §6): every file is checked as it is committed."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from serpsense.adapters.replay.recording import RECORDINGS, Recording
from serpsense.adapters.serp.redaction import IDENTITY_FIELDS, REVIEW_LISTS, REVIEWER_FIELDS

pytestmark = pytest.mark.security

FILES = sorted(RECORDINGS.glob("*.json"))


def fields(payload: Any, inside: str = "") -> Iterator[tuple[str, str]]:
    """Every field at any depth, with the review list it names a review in (a reviewer's name,
    avatar and link sit on the review itself; a developer's reply under it names the app's
    developer, not a person who reviewed)."""
    if isinstance(payload, dict):
        for key, item in payload.items():
            yield key, inside
            reviews = key in REVIEW_LISTS and isinstance(item, list)
            for value in item if reviews else [item]:
                yield from fields(value, key if reviews else "")
    elif isinstance(payload, list):
        for item in payload:
            yield from fields(item)


def test_the_demo_brands_are_recorded() -> None:
    assert {path.stem for path in FILES} >= {"ola", "uber", "rapido", "namma-yatri", "indrive"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_a_recording_holds_no_key_and_no_identity(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "api_key" not in text and not re.search(r"\b[0-9a-fA-F]{64}\b", text)
    recording = Recording.model_validate_json(text)  # refuses anything key-shaped too
    assert recording.brand == path.stem
    for payload in recording.payloads:
        found = set(fields(payload))
        assert not {key for key, _ in found} & IDENTITY_FIELDS
        assert not {key for key, inside in found if inside} & REVIEWER_FIELDS
        metadata = payload.get("search_metadata", {})
        assert not [v for v in metadata.values() if isinstance(v, str) and v.startswith("http")]
    assert not re.search(
        r'"[a-z_]*(?:key|token|secret|password)"\s*:', json.dumps(recording.payloads)
    )

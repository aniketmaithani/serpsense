"""Vendored front-end files are exactly the ones recorded in VENDORED.md."""

import hashlib
from pathlib import Path

import pytest

from serpsense.entrypoints.web.pages import STATIC

pytestmark = pytest.mark.unit

PINNED = {"chart.umd.js": "fed6a739f8d0f0687174de6cd14745fc0fc7809144ab113d22908a26bf0d7fea"}


@pytest.mark.parametrize(("name", "sha256"), PINNED.items())
def test_a_vendored_file_is_the_recorded_one(name: str, sha256: str) -> None:
    path: Path = STATIC / "vendor" / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    assert sha256 in (STATIC / "VENDORED.md").read_text()

"""Vendored front-end files are exactly the ones recorded in the README beside them."""

import hashlib
from pathlib import Path

import pytest

from serpsense.entrypoints.web.pages import STATIC

pytestmark = pytest.mark.unit

# file under static/ -> (its sha256, the README under static/ that records it)
PINNED = {
    "vendor/chart.umd.js": (
        "fed6a739f8d0f0687174de6cd14745fc0fc7809144ab113d22908a26bf0d7fea",
        "VENDORED.md",
    ),
    "vendor/anime/anime.esm.min.js": (
        "a19015a1a92d52025a2fb6703b6d67eadd1cc2aeaf880770e96e04cf6aa07be1",
        "vendor/anime/README.md",
    ),
    "fonts/space-grotesk-latin-400-normal.woff2": (
        "65fd17fcbd2e2f522940b5f67ead3d23329e02891aa5495e74d11a499c0b0673",
        "fonts/README.md",
    ),
    "fonts/space-grotesk-latin-500-normal.woff2": (
        "1b1a8131d9edf975d9decee81e2f2bf504812f7a4f498e5500f28a613e22e64c",
        "fonts/README.md",
    ),
    "fonts/space-grotesk-latin-700-normal.woff2": (
        "35f8aec56cfd5cbfdb03cc68733a54a0b05bb3617ffcd5fd332badc0b045ca55",
        "fonts/README.md",
    ),
    "fonts/jetbrains-mono-latin-400-normal.woff2": (
        "14425ba9c695763c1547f48a206b7aa60350a33ae23de09f0407877f3fcd89eb",
        "fonts/README.md",
    ),
}


@pytest.mark.parametrize(("name", "pinned"), PINNED.items())
def test_a_vendored_file_is_the_recorded_one(name: str, pinned: tuple[str, str]) -> None:
    sha256, readme = pinned
    path: Path = STATIC / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    assert sha256 in (STATIC / readme).read_text()

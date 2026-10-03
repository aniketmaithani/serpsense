"""Replay recordings (BUILD_PLAN §21, §23): real scans kept so the demo runs with no SerpApi or
Anthropic key (`SERPSENSE_MODE=replay`).

A recording is one brand's scans in the order they ran. Each scan lists the SerpApi answers it
got, keyed by the request the collectors made (engine and params, never the key); answers are
kept once in `payloads` and referred to by index, as a scan served from cache repeats them. The
labels are what the model said about each text the scans found, keyed by `text_id`: its source
and a short hash of the text the labeller sends. Recordings live in the package, so the image
ships them, and are parsed once here, at the boundary.

What goes into a recording is cleaned first (`cleaned`): payloads are already redacted when
stored (AGENTS §6), and SerpApi's follow-up links, pagination tokens and image links, which no
replayed scan reads, are dropped, since a secret scanner can't tell an opaque token or a content
hash from a key. A recording
that still holds anything shaped like a key is refused on load and on write.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from serpsense.domain.enums import MentionSource, SerpEngine, Topic
from serpsense.domain.search import contains_api_key, params_hash

FORMAT = 1
RECORDINGS = Path(__file__).parent / "recordings"
KEY_SHAPED = re.compile(r"\b[0-9a-f]{64}\b")  # a SerpApi key, or any sha256 hex
SERPAPI_LINK = "https://serpapi.com/"
# Read by no parser: pagination blocks, and image links, which can carry content hashes.
DROPPED_FIELDS = frozenset(
    {"serpapi_pagination", "pagination", "thumbnail", "thumbnails", "favicon"}
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RecordedAnswer(_Frozen):
    engine: SerpEngine
    params: dict[str, str]
    payload: int = Field(ge=0)  # an index into the recording's payloads

    @property
    def request_hash(self) -> str:
        return params_hash(self.engine, self.params)


class RecordedScan(_Frozen):
    recorded_at: AwareDatetime
    answers: tuple[RecordedAnswer, ...]


class RecordedLabel(_Frozen):
    text_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{24}$")]  # text_id(source, text)
    prompt_version: Annotated[str, StringConstraints(pattern=r"^[a-z_]+/v[1-9][0-9]*$")]
    is_about_brand: bool
    sentiment: Literal[-1, 0, 1]
    topic: Topic
    is_complaint: bool
    severity: int = Field(ge=0, le=100)
    reason: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class Recording(_Frozen):
    format: Literal[1]
    brand: str  # the brand's slug
    payloads: tuple[dict[str, Any], ...]
    scans: tuple[RecordedScan, ...]
    labels: tuple[RecordedLabel, ...]

    @model_validator(mode="after")
    def _check(self) -> "Recording":
        if any(a.payload >= len(self.payloads) for s in self.scans for a in s.answers):
            raise ValueError("an answer points past the recording's payloads")
        if leaks(self.payloads) or leaks([a.params for s in self.scans for a in s.answers]):
            raise ValueError("a recording must not hold anything shaped like a key")
        return self


def text_id(source: MentionSource, text: str) -> str:
    """How a label is found: the text's source and a short hash of the text (not 64 hex long,
    so it can't look like a key)."""
    return hashlib.sha256(f"{source.value}\n{text}".encode()).hexdigest()[:24]


def leaks(value: object) -> bool:
    """An `api_key` anywhere, or a string shaped like a SerpApi key."""
    text = json.dumps(value, ensure_ascii=False)
    return contains_api_key(value) or KEY_SHAPED.search(text) is not None


def cleaned(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A stored (already redacted) payload without what replay never reads and a secret scanner
    can't judge: SerpApi's own links, pagination blocks, `*_token` fields and image links."""
    return {key: _cleaned(item) for key, item in payload.items() if not _dropped(key, item)}


def _cleaned(value: Any) -> Any:
    if isinstance(value, Mapping):
        return cleaned(value)
    if isinstance(value, list):
        return [_cleaned(item) for item in value if not _dropped("", item)]
    return value


def _dropped(key: str, value: Any) -> bool:
    link = isinstance(value, str) and value.startswith(SERPAPI_LINK)
    return key in DROPPED_FIELDS or key.endswith("_token") or link


def load(directory: Path = RECORDINGS) -> tuple[Recording, ...]:
    """Every recording in the directory, by file name."""
    return tuple(
        Recording.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    )


def dump(recording: Recording) -> str:
    """The file a recording is kept in: compact UTF-8 JSON on one line, then a newline."""
    document = recording.model_dump(mode="json")
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"

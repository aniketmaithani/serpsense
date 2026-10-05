"""Replay recordings (BUILD_PLAN §21, §23): real scans kept so the demo runs with no SerpApi or
Anthropic key (`SERPSENSE_MODE=replay`).

A recording is one brand's scans in the order they ran. Each scan keeps its settings and the
SerpApi answers it got, keyed by the request the collectors made (engine and params, never the
key); answers are kept once in `payloads` and referred to by index, as a scan served from cache
repeats them. The model's output is kept per text, by `text_id` (its source and a short hash of
the text): the label it gave the text, and the story it put the text in, each with its prompt
version. A recording may also keep what the model said about an alert (by its rule, the level
reached and its story) and the replies it drafted for a story (by the story and the kind of
draft). Recordings live in the package, so the image ships them, and are parsed once here.

A payload keeps only what the parsers read (`cleaned`, an allow-list per engine): no SerpApi
links or tokens, no images, nothing that names a reviewer or a creator; a link keeps only the
query parameters that address its content (`kept_link`), so no tracking or sharing token
survives; and each Play review id is replaced by a hash of it, which keeps the review's
identity across scans but no link to the public review. A recording that still holds anything
shaped like a key is refused on load and on write.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from serpsense.domain.enums import AlertRule, DraftKind, MentionSource, SerpEngine, Topic
from serpsense.domain.search import contains_api_key, params_hash

FORMAT = 1
RECORDINGS = Path(__file__).parent / "recordings"
KEY_SHAPED = re.compile(r"\b[0-9a-fA-F]{64}\b")  # a SerpApi key, or any sha256 hex
REVIEW_ID = re.compile(r"^review-[0-9a-f]{24}$")

TextId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{24}$")]
PromptVersion = Annotated[str, StringConstraints(pattern=r"^[a-z_]+/v[1-9][0-9]*$")]

# What each engine's parser reads (adapters/serp/parsers.py), as nested allow-lists: a field
# mapped to None is kept as it is; one mapped to a dict keeps only those fields of each object
# under it. Engines no collector asks keep nothing. Update with the parsers.
Spec = Mapping[str, "Spec | None"]
LANGUAGE: dict[str, Spec | None] = {"search_parameters": {"hl": None}}
READ: Mapping[SerpEngine, Spec] = {
    SerpEngine.GOOGLE: LANGUAGE
    | {
        "organic_results": {"link": None, "title": None, "snippet": None, "position": None},
        "related_questions": {"question": None},
    },
    SerpEngine.GOOGLE_AUTOCOMPLETE: LANGUAGE | {"suggestions": {"value": None}},
    SerpEngine.GOOGLE_NEWS: LANGUAGE
    | {"news_results": {"link": None, "title": None, "iso_date": None, "source": {"name": None}}},
    SerpEngine.GOOGLE_TRENDS: LANGUAGE
    | {
        "related_queries": {"rising": {"query": None}, "top": {"query": None}},
        "interest_over_time": {
            "timeline_data": {
                "timestamp": None,
                "partial_data": None,
                "values": {"query": None, "query_index": None, "extracted_value": None},
            }
        },
    },
    SerpEngine.GOOGLE_PLAY_PRODUCT: LANGUAGE
    | {
        "product_info": {"rating": None, "reviews": None},
        "reviews": {"id": None, "snippet": None, "rating": None, "iso_date": None},
    },
}


# The query parameters a recorded link keeps, by host: what addresses the content (elsewhere `id`,
# as news sites address articles), never tracking, sharing or language (gclid, sig, si, hl, …).
LINK_PARAMS: Mapping[str, frozenset[str]] = {
    "play.google.com": frozenset({"id"}),
    "www.youtube.com": frozenset({"v"}),
    "youtube.com": frozenset({"v"}),
    "m.youtube.com": frozenset({"v"}),
}
ANY_HOST = frozenset({"id"})


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
    settings: dict[str, Any]  # the scan's settings snapshot: replayed, it asks the same requests
    answers: tuple[RecordedAnswer, ...]


class RecordedLabel(_Frozen):
    text_id: TextId
    prompt_version: PromptVersion
    is_about_brand: bool
    sentiment: Literal[-1, 0, 1]
    topic: Topic
    is_complaint: bool
    severity: int = Field(ge=0, le=100)
    reason: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class RecordedNarrative(_Frozen):
    """The story the model put a text in."""

    text_id: TextId
    prompt_version: PromptVersion
    label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class RecordedExplanation(_Frozen):
    """What the model said about an alert: found by its rule, the crisis level it reached
    ("none" before warm-up ends) and its story ("" when it is about none)."""

    rule: AlertRule
    level: Literal["none", "low", "medium", "high"]
    story_label: Annotated[str, StringConstraints(max_length=120)] = ""
    prompt_version: PromptVersion
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class RecordedDraft(_Frozen):
    """A reply the model drafted for a story, found by the story and the kind of draft, with the
    texts it cites."""

    story_label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    kind: DraftKind
    prompt_version: PromptVersion
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    cites: tuple[TextId, ...] = Field(min_length=1)


class Recording(_Frozen):
    format: Literal[1]
    brand: str  # the brand's slug
    name: str  # the brand's name, as the model was told it
    payloads: tuple[dict[str, Any], ...]
    scans: tuple[RecordedScan, ...]
    labels: tuple[RecordedLabel, ...]
    narratives: tuple[RecordedNarrative, ...] = ()
    explanations: tuple[RecordedExplanation, ...] = ()
    drafts: tuple[RecordedDraft, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "Recording":
        if any(a.payload >= len(self.payloads) for s in self.scans for a in s.answers):
            raise ValueError("an answer points past the recording's payloads")
        times = [scan.recorded_at for scan in self.scans]
        if times != sorted(set(times)):
            raise ValueError("scans are kept in the order they ran, one at a time")
        kept = [self.payloads, [s.settings for s in self.scans]]
        if leaks(kept) or leaks([a.params for s in self.scans for a in s.answers]):
            raise ValueError("a recording must not hold anything shaped like a key")
        return self


def text_id(source: MentionSource, text: str) -> str:
    """How a text's label and story are found: its source and a short hash of the text (not
    64 hex long, so it can't look like a key)."""
    return hashlib.sha256(f"{source.value}\n{text}".encode()).hexdigest()[:24]


def leaks(value: object) -> bool:
    """An `api_key` anywhere, or a string shaped like a SerpApi key."""
    text = json.dumps(value, ensure_ascii=False)
    return contains_api_key(value) or KEY_SHAPED.search(text) is not None


def cleaned(engine: SerpEngine, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Only what the engine's parser reads, with Play review ids replaced by their hash."""
    kept = _kept(payload, READ.get(engine, {}))
    if engine is SerpEngine.GOOGLE_PLAY_PRODUCT:
        for review in kept.get("reviews", []):
            if isinstance(review.get("id"), str):
                review["id"] = review_id(review["id"])
    return kept


def kept_link(link: str) -> str:
    """Only the query parameters that address the content, and a fragment only if a route."""
    parts = urlsplit(link)
    allowed = LINK_PARAMS.get(parts.netloc.lower(), ANY_HOST)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = urlencode([(name, value) for name, value in pairs if name in allowed])
    route = parts.fragment if parts.fragment.startswith(("/", "!")) else ""
    return urlunsplit(parts._replace(query=query, fragment=route))


def review_id(original: str) -> str:
    """A Play review's id as recordings keep it; an id already kept so is left alone."""
    if REVIEW_ID.match(original):
        return original
    return "review-" + hashlib.sha256(original.encode()).hexdigest()[:24]


def _kept(value: Mapping[str, Any], spec: Spec) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, item in value.items():
        if key not in spec:
            continue
        inner = spec[key]
        if isinstance(item, Mapping):
            if inner is not None:
                kept[key] = _kept(item, inner)
        elif isinstance(item, list):
            kept[key] = [_item(element, inner) for element in item if _wanted(element, inner)]
        else:
            kept[key] = kept_link(item) if key == "link" and isinstance(item, str) else item
    return kept


def _wanted(element: object, spec: Spec | None) -> bool:
    return not isinstance(element, Mapping | list) or (
        isinstance(element, Mapping) and spec is not None
    )


def _item(element: Any, spec: Spec | None) -> Any:
    return _kept(element, spec) if isinstance(element, Mapping) and spec is not None else element


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

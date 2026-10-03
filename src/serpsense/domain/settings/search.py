"""SerpApi settings for a brand's scans (BUILD_PLAN §6, data-model §2).

A document is resolved in layers (system defaults → user defaults → brand settings → a per-run
override) and the result is snapshotted into `scans.settings_snapshot`. Unknown keys are refused,
so a typo in a stored document can't silently fall back to a default. The defaults are the
plan's (§6.1, §6.2) and are the Standard preset.
"""

import re
import string
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

LANGUAGE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})*")
# The ranges Google Trends offers.
DateRange = Literal[
    "now 1-H", "now 4-H", "now 1-d", "now 7-d", "today 1-m", "today 3-m", "today 12-m", "today 5-y"
]


class Preset(StrEnum):
    LEAN = "lean"
    STANDARD = "standard"
    DEEP = "deep"


class ReviewSort(StrEnum):
    NEWEST = "newest"
    MOST_RELEVANT = "most_relevant"


def _template(value: str) -> str:
    """A search template names `{brand}` and nothing else: no other field, attribute or format."""
    try:
        fields = [(name, spec, conversion) for _, name, spec, conversion in _parse(value)]
    except ValueError as exc:
        raise ValueError("unbalanced braces in a template") from exc
    if not value.strip() or not fields or any(f != ("brand", "", None) for f in fields):
        raise ValueError("a template names {brand} and no other placeholder")
    return value


def _parse(value: str) -> list[tuple[str, str | None, str | None, str | None]]:
    return [part for part in string.Formatter().parse(value) if part[1] is not None]


def _term(value: str) -> str:
    if not value.strip():
        raise ValueError("a search term can't be blank")
    return value


def _distinct(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError("values must be distinct")
    return values


def _languages(values: tuple[str, ...]) -> tuple[str, ...]:
    if not all(LANGUAGE.fullmatch(v) for v in values):
        raise ValueError("languages are lowercase BCP-47 tags")
    return _distinct(values)


Text = Annotated[str, Field(max_length=100, pattern=r"^[^\x00]*$")]  # jsonb can't hold NUL
Template = Annotated[Text, AfterValidator(_template)]
Templates = Annotated[tuple[Template, ...], AfterValidator(_distinct)]
Terms = Annotated[tuple[Annotated[Text, AfterValidator(_term)], ...], AfterValidator(_distinct)]


class _Knobs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchPage(_Knobs):
    enabled: bool = True
    templates: Templates = Field(default=("{brand}",), min_length=1, max_length=3)
    pages: int = Field(default=1, ge=1, le=3)
    ai_overview: bool = True  # a follow-up call when the page only links to it


class Autocomplete(_Knobs):
    enabled: bool = True
    prefixes: Templates = Field(
        default=("{brand} ", "{brand} is ", "is {brand} "), min_length=1, max_length=5
    )


class News(_Knobs):
    enabled: bool = True
    extra_terms: Terms = Field(default=(), max_length=3)  # searched as "{brand} term"


class Trends(_Knobs):
    enabled: bool = True
    region: str = Field(default="IN", pattern=r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")
    date_range: DateRange = "today 3-m"
    related_queries: bool = True  # the brand's own related queries, every scan


class Play(_Knobs):
    """The product page (the rating) is always fetched, then `review_pages` pages of reviews in
    `review_sort` order; `review_pages=0` reads the rating only."""

    enabled: bool = True
    review_sort: ReviewSort = ReviewSort.NEWEST
    review_pages: int = Field(default=1, ge=0, le=3)


class Maps(_Knobs):
    enabled: bool = True
    review_sort: ReviewSort = ReviewSort.NEWEST
    review_pages: int = Field(default=1, ge=1, le=3)


class YouTube(_Knobs):
    enabled: bool = False
    templates: Templates = Field(default=("{brand}",), min_length=1, max_length=2)


class SearchSettings(_Knobs):
    country: str = Field(default="in", pattern=r"^[a-z]{2}$")
    languages: Annotated[tuple[str, ...], AfterValidator(_languages)] = Field(
        default=("en", "hi"), min_length=1
    )
    google_domain: str = Field(default="google.co.in", pattern=r"^google\.[a-z.]{2,10}$")
    serpapi_cache: bool = True  # SerpApi's own one-hour cache; cached results aren't billed
    max_searches: int = Field(default=25, ge=1, le=100)  # per scan; the admin limit still wins
    search_page: SearchPage = SearchPage()
    autocomplete: Autocomplete = Autocomplete()
    news: News = News()
    trends: Trends = Trends()
    play: Play = Play()
    maps: Maps = Maps()
    youtube: YouTube = YouTube()

    def capped(self, limit: int) -> "SearchSettings":
        """These settings under the admin's per-scan limit, which always wins (BUILD_PLAN §5)."""
        return self.model_copy(update={"max_searches": min(self.max_searches, limit)})


PRESETS: Mapping[Preset, Mapping[str, Any]] = {
    Preset.LEAN: {
        "languages": ("en",),
        "search_page": {"ai_overview": False},
        "trends": {"related_queries": False},
        "maps": {"enabled": False},
    },
    Preset.STANDARD: {},
    Preset.DEEP: {
        "search_page": {"templates": ("{brand}", "{brand} reviews", "{brand} complaints")},
        "news": {"extra_terms": ("complaint",)},
        "play": {"review_pages": 2},
        "maps": {"review_pages": 2},
        "youtube": {"enabled": True},
    },
}


def resolve(*layers: Mapping[str, Any]) -> SearchSettings:
    """Later layers win, merged per surface: system defaults, user defaults, brand, override."""
    merged: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
    return SearchSettings.model_validate(merged)


def preset(name: Preset, *overrides: Mapping[str, Any]) -> SearchSettings:
    return resolve(PRESETS[name], *overrides)

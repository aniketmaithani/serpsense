"""The search settings form: its fields, parsed once, and its sections.

The page shows one section at a time (no long scroll, and no script: the CSP allows none). The
other sections' current values ride along as hidden inputs, so the form always posts every knob
and saving one section leaves the rest as they were. A list is a textarea, one item a line, or
repeated hidden inputs, one item each; languages are one text field, "en, hi".
"""

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from serpsense.domain.settings.search import DateRange, ReviewSort
from serpsense.services.brand_settings import Knobs

RANGES: Mapping[str, str] = {  # Google Trends' ranges, as the page names them
    "now 1-H": "Past hour",
    "now 4-H": "Past 4 hours",
    "now 1-d": "Past day",
    "now 7-d": "Past 7 days",
    "today 1-m": "Past month",
    "today 3-m": "Past 3 months",
    "today 12-m": "Past 12 months",
    "today 5-y": "Past 5 years",
}
SEPARATORS = re.compile(r"[\s,]+")


class Section(StrEnum):
    GENERAL = "general"
    SEARCH = "search"
    AUTOCOMPLETE = "autocomplete"
    TRENDS = "trends"
    REVIEWS = "reviews"
    YOUTUBE = "youtube"


# Each section's name on the page and its knobs; every knob is in exactly one.
SECTIONS: Mapping[Section, tuple[str, tuple[str, ...]]] = {
    Section.GENERAL: (
        "General and limits",
        (
            "max_searches",
            "interval_minutes",
            "languages",
            "serpapi_cache",
            "country",
            "google_domain",
        ),
    ),
    Section.SEARCH: (
        "Google search",
        ("search_page", "search_pages", "search_templates", "ai_overview"),
    ),
    Section.AUTOCOMPLETE: (
        "Autocomplete and news",
        ("autocomplete", "autocomplete_prefixes", "news", "news_terms"),
    ),
    Section.TRENDS: ("Trends", ("trends", "related_queries", "trends_region", "trends_range")),
    Section.REVIEWS: (
        "Reviews",
        (
            "play",
            "play_review_pages",
            "play_review_sort",
            "maps",
            "maps_review_pages",
            "maps_review_sort",
        ),
    ),
    Section.YOUTUBE: ("YouTube", ("youtube", "youtube_templates")),
}


def _lines(value: object) -> object:
    """A textarea's items: its non-blank lines (repeated inputs' values joined first). Lines keep
    their spaces: an autocomplete prefix's trailing space asks for the next word."""
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    if isinstance(value, str):
        return tuple(line for line in value.splitlines() if line.strip())
    return value


def _codes(value: object) -> object:
    """Language tags separated by commas or spaces, lowercased; the service checks each."""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    if isinstance(value, str):
        return tuple(code.lower() for code in SEPARATORS.split(value) if code)
    return value


def _trimmed(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


Lines = Annotated[tuple[str, ...], BeforeValidator(_lines), Field(max_length=10)]
Codes = Annotated[tuple[str, ...], BeforeValidator(_codes), Field(max_length=10)]
Short = Annotated[str, BeforeValidator(_trimmed), Field(max_length=40)]


class SettingsForm(BaseModel):
    """The form as posted: unticked boxes are absent."""

    csrf_token: str = ""
    action: Literal["save", "preview"] = "save"
    section: Section = Section.GENERAL  # the one shown
    max_searches: int  # the service refuses values a scan can't use
    interval_minutes: int | None = None  # empty: manual scans only
    languages: Codes
    country: Short
    google_domain: Short
    search_pages: int
    search_templates: Lines
    autocomplete_prefixes: Lines
    news_terms: Lines = ()
    trends_region: Short
    trends_range: DateRange
    play_review_pages: int = 0
    play_review_sort: ReviewSort
    maps_review_pages: int
    maps_review_sort: ReviewSort
    youtube_templates: Lines
    serpapi_cache: bool = False
    search_page: bool = False
    ai_overview: bool = False
    autocomplete: bool = False
    news: bool = False
    trends: bool = False
    related_queries: bool = False
    play: bool = False
    maps: bool = False
    youtube: bool = False

    @field_validator("interval_minutes", mode="before")
    @classmethod
    def _manual(cls, value: object) -> object:
        return None if value == "" else value

    def knobs(self) -> Knobs:
        return Knobs(**self.model_dump(exclude={"csrf_token", "action", "section"}))


def section_of(text: str) -> Section:
    """The section a link names; anything else shows the first."""
    try:
        return Section(text)
    except ValueError:
        return Section.GENERAL


def hidden(knobs: Knobs, shown: Section) -> list[tuple[str, str]]:
    """The other sections' knobs as hidden inputs (name, value), as the form would post them."""
    pairs: list[tuple[str, str]] = []
    for section, (_, names) in SECTIONS.items():
        if section is not shown:
            for name in names:
                pairs += _posted(name, getattr(knobs, name))
    return pairs


def _posted(name: str, value: object) -> list[tuple[str, str]]:
    if isinstance(value, bool):
        return [(name, "true")] if value else []  # an unticked box is absent
    if isinstance(value, tuple):
        return [(name, str(item)) for item in value]
    return [(name, "" if value is None else str(value))]

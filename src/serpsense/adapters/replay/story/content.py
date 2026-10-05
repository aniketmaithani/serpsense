"""What the demo story is made of (adapters/replay/story): fictional brands, outlets and people.

VoltBox makes budget earbuds; SoundNest is its competitor. For a week nothing happens. Then the
VoltBox Air 2's charging case starts running hot, a forum thread and a few reviews say so, and on
the tenth day it is everywhere at once: reviews, the news, the search page, Google Trends and the
suggestions people see as they type. The story ends that morning, at the peak, as the landing page
shows it. SoundNest is untouched, so the trouble is VoltBox's own. Every link is on example.com.

Times are scan numbers, twelve hours apart (VoltBox is scanned at each, SoundNest at every other):
something with `at=19` is first shown by the scan on the evening of the tenth day.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from serpsense.domain.enums import Topic

SWELLING, REFUNDS = "Battery swelling", "Slow refunds"
STORIES = {
    SWELLING: "Buyers say the VoltBox Air 2 charging case overheats while charging and swells "
    "until the lid won't close; some say it scorched what it stood on.",
    REFUNDS: "Customers who returned VoltBox earbuds say refunds take weeks and support answers "
    "with templates.",
}


@dataclass(frozen=True)
class Tag:
    """How the model labelled a text, and the story it put the text in."""

    sentiment: int
    topic: Topic
    severity: int
    reason: str
    story: str | None = None
    about: bool = True

    @property
    def complaint(self) -> bool:
        return self.sentiment < 0


def good(reason: str, topic: Topic = Topic.PRODUCT_QUALITY) -> Tag:
    return Tag(1, topic, 0, reason)


def plain(reason: str, topic: Topic = Topic.OTHER) -> Tag:
    return Tag(0, topic, 0, reason)


def bad(
    severity: int, reason: str, story: str | None = SWELLING, topic: Topic = Topic.SAFETY
) -> Tag:
    return Tag(-1, topic, severity, reason, story)


@dataclass(frozen=True)
class Result:
    at: int
    rank: int  # lower ranks higher on the page
    title: str
    snippet: str
    link: str
    tag: Tag


@dataclass(frozen=True)
class Question:
    at: int
    rank: int
    text: str
    tag: Tag


@dataclass(frozen=True)
class Suggestion:
    at: int
    rank: int
    value: str  # suggestions aren't labelled yet (no prompt), so they carry no tag


@dataclass(frozen=True)
class Article:
    at: int
    title: str
    outlet: str
    link: str
    tag: Tag


@dataclass(frozen=True)
class Review:
    at: int
    stars: int
    text: str
    tag: Tag


@dataclass(frozen=True)
class Query:
    at: int
    query: str
    rising: bool
    tag: Tag


@dataclass(frozen=True)
class Content:
    results: tuple[Result, ...] = ()
    questions: tuple[Question, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()
    articles: tuple[Article, ...] = ()
    reviews: tuple[Review, ...] = ()
    queries: tuple[Query, ...] = ()
    rating: tuple[tuple[int, float], ...] = ()  # (from scan, stars)


OFF = {"enabled": False}


@dataclass(frozen=True)
class StoryBrand:
    name: str
    slug: str
    app: str  # its Google Play id
    settings: Mapping[str, Any]  # a search settings document (domain/settings/search.py)
    every: int  # scans: VoltBox at every step, SoundNest at every other
    offset: timedelta  # after the step, so the two never scan at once
    content: Content

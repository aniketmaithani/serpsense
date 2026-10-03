"""Port for collectors: what a scan searches for, and what the answers held.

A collector turns a scan's target (its brand, competitors, apps and settings) into SerpApi
requests, and reads each redacted answer into mentions and observations (ADR-0007). It never
calls SerpApi: the scan's collector runner sends the requests through the search service, so
every call is cached, budgeted and recorded. An answer can lead to more requests (the next page
of results, an AI Overview the page only links to), which `Reading.follow_ups` carries.

A collector may serve more than one surface (the search page also shows the AI Overview), so
each lead names the surface it serves and the others its answer may show, and a reading says
which surfaces had nothing to show.
Collectors are registered with the runner (AGENTS §1), so a new surface or engine is a new
collector, not a change to the scan service.
"""

import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from serpsense.domain.enums import Surface
from serpsense.domain.mention import ParsedMention
from serpsense.domain.observation import MAX_COMPARISON, AppRating, TrendsPoint
from serpsense.domain.search import InvalidSearchParams, canonical_params
from serpsense.domain.settings.search import SearchSettings
from serpsense.ports.search_provider import SearchRequest

# Joiners some Indic spellings need; every other format character (bidi controls, zero-width
# spaces) is refused.
JOINERS = frozenset({"\u200c", "\u200d"})


@dataclass(frozen=True)
class Subject:
    """A brand as searches name it: whitespace collapsed, no control or invisible characters
    other than the joiners."""

    brand_id: uuid.UUID
    name: str

    def __post_init__(self) -> None:
        name = " ".join(self.name.split())
        if any(_invisible(char) for char in name):
            raise ValueError("a brand name has no control or invisible characters")
        if not name:
            raise ValueError("a brand is searched by a non-blank name")
        _sendable("a brand name", name)
        object.__setattr__(self, "name", name)


def _invisible(char: str) -> bool:
    return unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char not in JOINERS


def _sendable(what: str, value: str) -> None:
    """Refuse a value no request could carry, so one bad row can't stop a scan's leads."""
    try:
        canonical_params({"q": value})
    except InvalidSearchParams as exc:
        raise ValueError(f"{what} can't be sent to SerpApi") from exc


@dataclass(frozen=True)
class App:
    """One of the brand's Google Play apps."""

    brand_app_id: uuid.UUID
    package: str  # the store id, e.g. com.olacabs.customer

    def __post_init__(self) -> None:
        if not 1 <= len(self.package) <= 255 or any(char.isspace() for char in self.package):
            raise ValueError("a store id is 1-255 characters without whitespace")
        _sendable("a store id", self.package)


@dataclass(frozen=True)
class Target:
    """What a scan collects about: the brand, the competitors Trends compares it with, its apps,
    and the scan's settings snapshot."""

    brand: Subject
    settings: SearchSettings
    competitors: tuple[Subject, ...] = ()
    apps: tuple[App, ...] = ()

    def __post_init__(self) -> None:
        brands = [self.brand.brand_id, *(c.brand_id for c in self.competitors)]
        if len(brands) > MAX_COMPARISON:
            raise ValueError("a brand has at most four competitors")
        if len(set(brands)) != len(brands):
            raise ValueError("a brand is compared with each competitor once")
        if len({app.brand_app_id for app in self.apps}) != len(self.apps):
            raise ValueError("each app once")


@dataclass(frozen=True)
class Lead:
    """One request to send, the surface it serves, and what its answer is about."""

    surface: Surface
    request: SearchRequest
    also: frozenset[Surface] = frozenset()  # other surfaces its answer may show
    brand_app_id: uuid.UUID | None = None  # the app whose page or reviews it asks for
    subjects: tuple[uuid.UUID, ...] = ()  # a Trends comparison's brands, in query order
    page: int = 1  # page `page` of `pages` of this search; each page may lead to the next
    pages: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.page <= self.pages:
            raise ValueError("a page is counted from 1 up to the number of pages")
        if self.surface in self.also:
            raise ValueError("`also` names the other surfaces an answer may show")


@dataclass(frozen=True)
class Reading:
    """What one answer held, and the request that follows from it, if any."""

    mentions: tuple[ParsedMention, ...] = ()
    rating: AppRating | None = None  # the app's store rating, from its product page
    trends: tuple[TrendsPoint, ...] = ()  # each point's `query_index` is a place in `subjects`
    not_shown: frozenset[Surface] = frozenset()  # surfaces the answer showed nothing for
    follow_ups: tuple[Lead, ...] = ()


class Collector(Protocol):
    def enabled(self, target: Target) -> frozenset[Surface]:
        """The surfaces this collector collects for the target; a surface no collector enables
        is recorded as disabled."""
        ...

    def leads(self, target: Target) -> Sequence[Lead]:
        """The first requests, most important first. With every follow-up they can lead to,
        they are at most the searches `domain.estimator` counts, so the budget check holds.
        Each lead's surface, and the surfaces it may also show, are enabled."""
        ...

    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        """What one redacted answer held. Never raises on the payload's content: an item that
        can't make a valid mention or point is skipped."""
        ...

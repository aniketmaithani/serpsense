"""Surface scores and health (BUILD_PLAN §11, docs/scoring.md).

Pure functions over what one scan saw, with labels from the model (sentiment -1/0/1). Every
score is an integer 0-100; a surface that showed nothing scores None and drops out of health,
whose weights are re-spread over the surfaces that did. Mentions that only share the brand's
name are filtered out before scoring.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from serpsense.domain.enums import Surface

NEWEST_REVIEWS = 50  # Play and Maps read the sentiment of the newest 50 reviews
UNRANKED = 10  # a search result without a rank weighs like the tenth

HEALTH_WEIGHTS_BP: Mapping[Surface, int] = {
    Surface.SEARCH_PAGE: 2500,
    Surface.AUTOCOMPLETE: 2000,
    Surface.AI_OVERVIEW: 1500,
    Surface.NEWS: 1500,
    Surface.PLAY: 1500,
    Surface.MAPS: 1000,
}


@dataclass(frozen=True)
class Seen:
    """One mention about the brand as the scan saw it."""

    sentiment: int  # -1, 0, 1
    position: int | None = None  # rank on its surface, from 1

    def __post_init__(self) -> None:
        if self.sentiment not in (-1, 0, 1):
            raise ValueError("sentiment is -1, 0 or 1")
        if self.position is not None and self.position < 1:
            raise ValueError("a rank starts at 1")


def half_up(value: float) -> int:
    """Round half up, as Postgres rounds a positive numeric (Python's round() goes to even)."""
    return math.floor(value + 0.5)


def ratio(part: int, whole: int) -> int:
    """100 times part over whole, rounded half up exactly in integers (no float error at .5)."""
    return (200 * part + whole) // (2 * whole)


def search_page(results: Sequence[Seen]) -> int | None:
    """100 minus the negative share (rounded half up), each result weighted 1/log2(rank + 1)."""
    if not results:
        return None
    weights = [1 / math.log2((r.position or UNRANKED) + 1) for r in results]
    negative = sum(w for r, w in zip(results, weights, strict=True) if r.sentiment < 0)
    return 100 - half_up(100 * negative / sum(weights))


def autocomplete_penalty(rank: int | None) -> int:
    """A negative suggestion costs 30, more the higher it sits: 45, 40, 35 for the top three."""
    return max(30, 50 - 5 * rank) if rank else 30


def autocomplete(suggestions: Sequence[Seen]) -> int | None:
    if not suggestions:
        return None
    penalty = sum(autocomplete_penalty(s.position) for s in suggestions if s.sentiment < 0)
    return max(0, 100 - penalty)


def ai_overview(overview: Seen | None) -> int | None:
    return None if overview is None else 50 + 50 * overview.sentiment


def news(articles: Sequence[Seen]) -> int | None:
    """(positive + half the neutral) over all articles of the last seven days."""
    if not articles:
        return None
    positive = sum(a.sentiment > 0 for a in articles)
    neutral = sum(a.sentiment == 0 for a in articles)
    return ratio(2 * positive + neutral, 2 * len(articles))


def reviews(rating_hundredths: int | None, newest: Sequence[Seen]) -> int | None:
    """Half the store rating (1-5 stars as 0-100), half the sentiment of the newest reviews
    (newest first); either half alone when the other is missing."""
    parts = []
    if rating_hundredths is not None:
        if not 100 <= rating_hundredths <= 500:
            raise ValueError("a rating is 1.00-5.00 stars")
        parts.append(ratio(rating_hundredths - 100, 400))
    recent = newest[:NEWEST_REVIEWS]
    if recent:
        parts.append(ratio(sum(r.sentiment for r in recent) + len(recent), 2 * len(recent)))
    return ratio(sum(parts), 100 * len(parts)) if parts else None


def health(scores: Mapping[Surface, int | None]) -> int | None:
    """The weighted mean of the surfaces that showed something; None when none did."""
    seen = {s: v for s, v in scores.items() if v is not None and s in HEALTH_WEIGHTS_BP}
    if any(not 0 <= v <= 100 for v in seen.values()):
        raise ValueError("a surface score is 0-100")
    if not seen:
        return None
    weighted = sum(HEALTH_WEIGHTS_BP[s] * v for s, v in seen.items())
    return ratio(weighted, 100 * sum(HEALTH_WEIGHTS_BP[s] for s in seen))

"""One scan's scores from what it saw (docs/scoring.md).

The inputs are the scan's labelled mentions about the brand (with whether each was first seen in
this scan), its app ratings, the sentiment of the brand's newest reviews, and, for the crisis,
how many negative mentions each of the brand's earlier scans saw first. A surface's mentions
count as new only once an earlier scan has collected that surface: its first collection is the
baseline, not a crisis.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

from serpsense.domain.enums import CrisisComponent, MentionSource, Surface
from serpsense.domain.mention import SURFACE
from serpsense.domain.scoring import crisis as rules
from serpsense.domain.scoring import surfaces
from serpsense.domain.scoring.surfaces import Seen, ratio

# The surfaces health and the crisis read; Trends and YouTube aren't among them.
HEALTH_SURFACES = frozenset(surfaces.HEALTH_WEIGHTS_BP)
RESULTS = frozenset({MentionSource.SERP_RESULT, MentionSource.TOP_STORY})  # not the questions
NEWS_WINDOW = timedelta(days=7)
PRESS_WINDOW = timedelta(hours=48)
RISING = 25  # related queries ranked 1-25 are the rising ones (adapters/serp/parsers.py)


@dataclass(frozen=True)
class Observed:
    """A mention about the brand this scan saw, with its label."""

    source: MentionSource
    sentiment: int  # -1, 0 or 1
    severity: int = 0  # 0-100
    position: int | None = None
    published_at: datetime | None = None
    new: bool = False  # first seen for the brand in this scan


@dataclass(frozen=True)
class Earlier:
    """One of the brand's earlier scored scans: the negatives it saw first, per surface, and the
    surfaces a scan before it had collected."""

    new_negative: Mapping[Surface, int]
    collected_before: frozenset[Surface]

    def counted(self, surface: Surface) -> int:
        return self.new_negative.get(surface, 0) if surface in self.collected_before else 0


@dataclass(frozen=True)
class ScoreInputs:
    at: datetime  # when the scan was made
    observed: tuple[Observed, ...]
    ratings: tuple[int, ...] = ()  # the brand's app ratings in this scan, in hundredths
    newest_reviews: tuple[int, ...] = ()  # sentiments of its newest reviews, newest first
    earlier: tuple[Earlier, ...] = ()  # newest first
    collected_before: frozenset[Surface] = frozenset()  # surfaces earlier scans collected


@dataclass(frozen=True)
class ScanScores:
    surfaces: Mapping[Surface, int]  # only the surfaces that showed something
    components: Mapping[CrisisComponent, int]  # all of them


def score(inputs: ScoreInputs) -> ScanScores:
    shown = {s: v for s, v in _surface_scores(inputs).items() if v is not None}
    return ScanScores(MappingProxyType(shown), MappingProxyType(_components(inputs, shown)))


def _surface_scores(inputs: ScoreInputs) -> dict[Surface, int | None]:
    def of(*sources: MentionSource) -> list[Observed]:
        return [o for o in inputs.observed if o.source in sources]

    week = [o for o in of(MentionSource.NEWS) if _within(o, inputs.at, NEWS_WINDOW)]
    overview = _seen(of(MentionSource.AI_OVERVIEW))
    rating = ratio(sum(inputs.ratings), 100 * len(inputs.ratings)) if inputs.ratings else None
    return {
        Surface.SEARCH_PAGE: surfaces.search_page(_seen(of(*RESULTS))),
        Surface.AUTOCOMPLETE: surfaces.autocomplete(_seen(of(MentionSource.AUTOCOMPLETE))),
        Surface.AI_OVERVIEW: surfaces.ai_overview(overview[0] if overview else None),
        Surface.NEWS: surfaces.news(_seen(week)),
        Surface.PLAY: surfaces.reviews(rating, [Seen(s) for s in inputs.newest_reviews]),
    }


def _components(inputs: ScoreInputs, shown: Mapping[Surface, int]) -> dict[CrisisComponent, int]:
    new = [o for o in inputs.observed if o.new and o.sentiment < 0]
    new = [o for o in new if SURFACE[o.source] in inputs.collected_before]  # not the baseline
    per_surface = {s: sum(SURFACE[o.source] is s for o in new) for s in HEALTH_SURFACES}
    totals = [sum(e.counted(s) for s in HEALTH_SURFACES) for e in inputs.earlier]
    spread = [
        rules.SurfaceNegatives(per_surface[s], [e.counted(s) for e in inputs.earlier])
        for s in shown
        if s in HEALTH_SURFACES
    ]
    return {
        CrisisComponent.VELOCITY: rules.velocity(sum(per_surface.values()), totals),
        CrisisComponent.SPREAD: rules.spread(spread),
        CrisisComponent.AUTOCOMPLETE: rules.new_negative_autocomplete(
            [o.position for o in new if o.source is MentionSource.AUTOCOMPLETE]
        ),
        CrisisComponent.TRENDS: rules.rising_negative_trends(
            sum(o.source is MentionSource.TRENDS_QUERY and _rising(o) for o in new)
        ),
        CrisisComponent.PRESS: rules.press(
            [
                o.severity
                for o in new
                if o.source is MentionSource.NEWS and _within(o, inputs.at, PRESS_WINDOW)
            ]
        ),
    }


def _seen(observed: Iterable[Observed]) -> list[Seen]:
    return [Seen(o.sentiment, o.position) for o in observed]


def _within(observed: Observed, at: datetime, window: timedelta) -> bool:
    """Published in the window before the scan was made, or since: a scan collects a little
    after it is made."""
    return observed.published_at is not None and observed.published_at >= at - window


def _rising(observed: Observed) -> bool:
    return observed.position is not None and observed.position <= RISING

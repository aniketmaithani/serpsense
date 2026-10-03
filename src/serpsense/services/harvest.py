"""What a scan's collection gives the stores (BUILD_PLAN §10 step 5, data-model §5).

The collector runner hands back every answer it read; `harvest` turns them into what the stores
take: one sighting for each app's Play reviews and one for everything else, each app's rating
(the first its product page showed), the Trends comparison, and a result for every surface. A
scan observes each mention once, at its best rank, so a source is never split across sightings.
A review whose lead doesn't name its app (or a Maps review, until leads name places) and a
comparison the store would refuse are dropped and logged, so one bad answer can't sink the rest.
`keep` writes a harvest in the caller's unit of work; every store ignores what a retried scan
already wrote.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import ParsedMention
from serpsense.domain.observation import AppRating
from serpsense.observability import get_logger
from serpsense.ports.collector import Lead, Reading
from serpsense.ports.mention_store import Sighting
from serpsense.ports.observation_store import Comparison
from serpsense.ports.scan_store import SurfaceResult
from serpsense.ports.unit_of_work import UnitOfWork
from serpsense.services.collection import Collected

log = get_logger(__name__)

Readings = Sequence[tuple[Lead, Reading]]


@dataclass(frozen=True)
class Harvest:
    scan_id: uuid.UUID
    sightings: tuple[Sighting, ...]
    ratings: Mapping[uuid.UUID, AppRating]  # by brand app, as its product page showed it
    comparison: Comparison | None  # the Trends joint query, when it was answered
    results: tuple[SurfaceResult, ...]


@dataclass(frozen=True)
class Kept:
    new_mentions: int
    revised: int  # reviews whose text changed since they were last seen
    observed: int  # mention observations written
    ratings: int
    trends_points: int


def harvest(scan_id: uuid.UUID, collected: Collected) -> Harvest:
    results = tuple(
        SurfaceResult(surface, outcome.outcome, outcome.error_code)
        for surface, outcome in collected.outcomes.items()
    )
    readings = collected.readings
    return Harvest(
        scan_id,
        _sightings(scan_id, readings),
        _ratings(readings),
        _comparison(scan_id, readings),
        results,
    )


def keep(uow: UnitOfWork, crop: Harvest, *, at: datetime) -> Kept:
    uow.scans.record_surfaces(crop.scan_id, crop.results)
    new = revised = observed = 0
    for sighting in crop.sightings:
        recorded = uow.mentions.record(sighting, at=at)
        new, revised = new + recorded.new, revised + recorded.revised
        observed += recorded.observed
    ratings = sum(
        uow.observations.record_app_rating(crop.scan_id, app, rating)
        for app, rating in crop.ratings.items()
    )
    points = uow.observations.record_trends(crop.comparison) if crop.comparison else 0
    return Kept(new, revised, observed, ratings, points)


def _sightings(scan_id: uuid.UUID, readings: Readings) -> tuple[Sighting, ...]:
    by_app: dict[uuid.UUID | None, list[ParsedMention]] = {}
    dropped: dict[MentionSource, int] = {}
    for lead, reading in readings:
        for mention in reading.mentions:
            review = mention.source is MentionSource.PLAY_REVIEW
            if (
                review and lead.brand_app_id is None
            ) or mention.source is MentionSource.MAPS_REVIEW:
                dropped[mention.source] = dropped.get(mention.source, 0) + 1
                continue
            by_app.setdefault(lead.brand_app_id if review else None, []).append(mention)
    for source, count in dropped.items():
        log.warning("harvest.mentions_dropped", source=source, count=count)
    return tuple(Sighting(scan_id, tuple(seen), brand_app_id=app) for app, seen in by_app.items())


def _ratings(readings: Readings) -> Mapping[uuid.UUID, AppRating]:
    ratings: dict[uuid.UUID, AppRating] = {}
    for lead, reading in readings:
        if reading.rating is not None and lead.brand_app_id is not None:
            ratings.setdefault(lead.brand_app_id, reading.rating)
    return MappingProxyType(ratings)


def _comparison(scan_id: uuid.UUID, readings: Readings) -> Comparison | None:
    for lead, reading in readings:
        if lead.subjects:
            try:
                return Comparison(scan_id, lead.subjects, reading.trends)
            except ValueError:
                log.warning("harvest.comparison_dropped", points=len(reading.trends))
                return None
    return None

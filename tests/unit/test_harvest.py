"""A scan's collection as the stores take it, and kept in one unit of work."""

import uuid
from datetime import UTC, datetime
from types import MappingProxyType

import pytest
from structlog.testing import capture_logs

from serpsense.domain.collection import Outcome
from serpsense.domain.enums import MentionSource, ScanTrigger, SerpEngine, Surface, SurfaceOutcome
from serpsense.domain.mention import ParsedMention, text_key
from serpsense.domain.observation import AppRating, TrendsPoint
from serpsense.ports.collector import Lead, Reading
from serpsense.ports.scan_store import NewScan, SurfaceResult
from serpsense.ports.search_provider import SearchRequest
from serpsense.services.collection import Collected
from serpsense.services.harvest import Kept, harvest, keep
from tests.fakes import FakeUnitOfWork, InMemoryScans, StaticSchedules

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
CABS, PAY = uuid.uuid4(), uuid.uuid4()
OLA, UBER = uuid.uuid4(), uuid.uuid4()
ASK = SearchRequest(SerpEngine.GOOGLE, {"q": "Ola"})


def said(source: MentionSource, text: str, rank: int = 1) -> ParsedMention:
    identity = f"gp:{text}" if source is MentionSource.PLAY_REVIEW else text_key(text)
    return ParsedMention(source, identity, text, position=rank)


def point(index: int, interest: int) -> TrendsPoint:
    return TrendsPoint("q", index, NOW, interest, is_partial=False)


READINGS = (
    (Lead(Surface.NEWS, ASK), Reading(mentions=(said(MentionSource.NEWS, "Ola fares up"),))),
    (
        Lead(Surface.PLAY, ASK, brand_app_id=CABS),
        Reading(
            mentions=(said(MentionSource.PLAY_REVIEW, "late driver"),),
            rating=AppRating(rating_hundredths=410, review_count=900),
        ),
    ),
    (
        Lead(Surface.PLAY, ASK, brand_app_id=CABS, pages=2),
        Reading(
            mentions=(said(MentionSource.PLAY_REVIEW, "late driver", rank=3),),
            rating=AppRating(rating_hundredths=390, review_count=950),  # the first one counts
        ),
    ),
    (
        Lead(Surface.PLAY, ASK, brand_app_id=PAY),  # no rating shown; a mention that isn't a review
        Reading(mentions=(said(MentionSource.NEWS, "Ola Money down"),)),
    ),
    (
        Lead(Surface.TRENDS, ASK, subjects=(OLA, UBER)),
        Reading(trends=(point(0, 70), point(1, 90))),
    ),
    (Lead(Surface.TRENDS, ASK, subjects=(UBER,)), Reading(trends=(point(0, 10),))),
    (
        Lead(Surface.AUTOCOMPLETE, ASK),
        Reading(mentions=(said(MentionSource.AUTOCOMPLETE, "ola refund"),)),
    ),
)
OUTCOMES = MappingProxyType(
    {
        Surface.NEWS: Outcome(SurfaceOutcome.SUCCEEDED),
        Surface.PLAY: Outcome(SurfaceOutcome.FAILED, "serpapi.http_5xx"),
        Surface.MAPS: Outcome(SurfaceOutcome.DISABLED),
    }
)


def test_reviews_are_seen_per_app_and_everything_else_together() -> None:
    scan_id = uuid.uuid4()
    crop = harvest(scan_id, Collected(OUTCOMES, READINGS))
    assert [(s.brand_app_id, [m.text for m in s.mentions]) for s in crop.sightings] == [
        (None, ["Ola fares up", "Ola Money down", "ola refund"]),
        (CABS, ["late driver", "late driver"]),  # both pages: the store keeps the best rank
    ]
    assert {s.scan_id for s in crop.sightings} == {scan_id}
    assert dict(crop.ratings) == {CABS: AppRating(410, 900)}  # PAY's page showed no rating
    assert crop.comparison is not None
    assert (crop.comparison.subjects, len(crop.comparison.points)) == ((OLA, UBER), 2)
    assert crop.results == (
        SurfaceResult(Surface.NEWS, SurfaceOutcome.SUCCEEDED),
        SurfaceResult(Surface.PLAY, SurfaceOutcome.FAILED, "serpapi.http_5xx"),
        SurfaceResult(Surface.MAPS, SurfaceOutcome.DISABLED),
    )


def test_a_scan_without_a_trends_answer_has_no_comparison() -> None:
    crop = harvest(uuid.uuid4(), Collected(OUTCOMES, READINGS[:1]))
    assert (crop.comparison, dict(crop.ratings), len(crop.sightings)) == (None, {}, 1)
    assert harvest(uuid.uuid4(), Collected(OUTCOMES, ())).sightings == ()


def test_a_harvest_is_kept_in_one_unit_of_work() -> None:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    new = NewScan(uuid.uuid4(), ScanTrigger.MANUAL, {}, 9, NOW, requested_by=uuid.uuid4())
    scan_id = uow.scans.create(new)
    assert scan_id is not None
    crop = harvest(scan_id, Collected(OUTCOMES, READINGS))
    uow.mentions.known.add((MentionSource.NEWS, text_key("Ola fares up")))  # seen last week
    with uow:
        kept = keep(uow, crop, at=NOW)
    assert kept == Kept(new_mentions=3, revised=0, observed=4, ratings=1, trends_points=2)
    assert set(uow.scans.surfaces[scan_id]) == {Surface.NEWS, Surface.PLAY, Surface.MAPS}
    assert uow.mentions.sightings == list(crop.sightings)
    assert uow.observations.ratings == [(scan_id, CABS, AppRating(410, 900))]
    assert uow.observations.comparisons == [crop.comparison]
    with uow:
        again = keep(uow, crop, at=NOW)  # a retried scan writes nothing new
    assert (again.new_mentions, again.observed) == (0, 0)


def test_reviews_their_lead_doesnt_place_are_dropped_and_logged() -> None:
    orphan = said(MentionSource.PLAY_REVIEW, "no app")
    place = ParsedMention(MentionSource.MAPS_REVIEW, "maps:1", "rude staff", star_rating=1)
    readings = ((Lead(Surface.PLAY, ASK), Reading(mentions=(orphan, place))), *READINGS[:1])
    with capture_logs() as logs:
        crop = harvest(uuid.uuid4(), Collected(OUTCOMES, readings))
    assert [[m.text for m in s.mentions] for s in crop.sightings] == [["Ola fares up"]]
    dropped = [(e["source"], e["count"]) for e in logs if e["event"] == "harvest.mentions_dropped"]
    assert dropped == [(MentionSource.PLAY_REVIEW, 1), (MentionSource.MAPS_REVIEW, 1)]


def test_a_comparison_the_store_would_refuse_is_dropped() -> None:
    off_the_chart = (Lead(Surface.TRENDS, ASK, subjects=(OLA,)), Reading(trends=(point(1, 50),)))
    with capture_logs() as logs:
        crop = harvest(uuid.uuid4(), Collected(OUTCOMES, (off_the_chart, *READINGS[:1])))
    assert crop.comparison is None and len(crop.sightings) == 1
    assert [e["event"] for e in logs] == ["harvest.comparison_dropped"]

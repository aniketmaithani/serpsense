"""Trends and Play collectors for Ola, and every collector against the estimator."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from serpsense.adapters.serp.collectors import COLLECTORS, PlayCollector, TrendsCollector
from serpsense.domain.enums import MentionSource, SerpEngine, Surface
from serpsense.domain.estimator import BrandFacts, estimate
from serpsense.domain.settings.search import Preset, preset, resolve
from serpsense.ports.collector import App, Collector, Lead, Subject, Target

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parents[1] / "fixtures" / "serpapi" / "ola"
OLA = Subject(uuid.uuid4(), "Ola")
RIVALS = tuple(Subject(uuid.uuid4(), name) for name in ("Uber", "Rapido", "Namma Yatri", "inDrive"))
CABS = App(uuid.uuid4(), "com.olacabs.customer")
OLA_PAY = App(uuid.uuid4(), "com.olacabs.olamoney")
# Parameters SerpApi echoes with its own defaults when a request leaves them out.
DEFAULTS = frozenset({"device", "hl", "platform", "sort_by", "tz"})


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def assert_answers(lead: Lead, name: str) -> None:
    """The recorded answer is to exactly this request: everything sent is echoed back, and
    anything else echoed is one of SerpApi's own defaults."""
    sent = {"engine": lead.request.engine.value, **lead.request.params}
    echoed = fixture(name)["search_parameters"]
    assert sent.items() <= echoed.items()
    assert echoed.keys() - sent.keys() <= DEFAULTS


def target(competitors: tuple[Subject, ...] = RIVALS, **settings: Any) -> Target:
    return Target(OLA, resolve(settings), competitors=competitors, apps=(CABS, OLA_PAY))


def test_trends_compares_the_brand_with_all_four_competitors_in_one_query() -> None:
    over_time, related = TrendsCollector().leads(target())
    assert_answers(over_time, "trends_timeseries")
    assert over_time.subjects == (OLA.brand_id, *(rival.brand_id for rival in RIVALS))
    assert_answers(related, "trends_related")
    assert related.subjects == ()  # the brand's own related queries
    assert {over_time.surface, related.surface} == {Surface.TRENDS}
    points = TrendsCollector().read(over_time, fixture("trends_timeseries")).trends
    assert {point.query_index for point in points} == {0, 1, 2, 3, 4}
    mentions = TrendsCollector().read(related, fixture("trends_related")).mentions
    assert mentions and {m.source for m in mentions} == {MentionSource.TRENDS_QUERY}


def test_trends_keeps_the_first_point_for_each_line_it_asked_for_and_time() -> None:
    sent = TrendsCollector().leads(target(competitors=()))[0]
    alone = Lead(Surface.TRENDS, sent.request, subjects=(OLA.brand_id,))
    payload = fixture("trends_timeseries")
    timeline = payload["interest_over_time"]["timeline_data"]
    doubled = json.loads(json.dumps(timeline))
    for moment in doubled:
        for value in moment["values"]:
            value["extracted_value"] = 0  # a second, different point for each line and time
    payload["interest_over_time"]["timeline_data"] = timeline + doubled
    points = TrendsCollector().read(alone, payload).trends
    assert {p.query_index for p in points} == {0} and len(points) == 6  # Ola's six days
    assert points[0].interest == 77  # the first given


def test_a_name_holding_a_comma_is_left_out_of_trends() -> None:
    comma = Subject(uuid.uuid4(), "Uber, Inc")
    over_time, _ = TrendsCollector().leads(target(competitors=(comma, RIVALS[1])))
    assert (over_time.request.params["q"], len(over_time.subjects)) == ("Ola,Rapido", 2)
    brand = Target(Subject(uuid.uuid4(), "Ola, Inc"), resolve({}))
    assert (TrendsCollector().enabled(brand), TrendsCollector().leads(brand)) == (frozenset(), [])


def test_trends_asks_only_for_what_is_on() -> None:
    assert TrendsCollector().enabled(target()) == {Surface.TRENDS}
    off = target(trends={"enabled": False})
    assert (TrendsCollector().enabled(off), TrendsCollector().leads(off)) == (frozenset(), [])
    (over_time,) = TrendsCollector().leads(target(trends={"related_queries": False}))
    assert over_time.request.params["data_type"] == "TIMESERIES"


def test_play_reads_every_product_page_before_any_review_page() -> None:
    leads = PlayCollector().leads(target(play={"review_pages": 2}))
    assert [(lead.brand_app_id, lead.pages) for lead in leads] == [
        (CABS.brand_app_id, 1),
        (OLA_PAY.brand_app_id, 1),
        (CABS.brand_app_id, 2),
        (OLA_PAY.brand_app_id, 2),
    ]
    packages = [lead.request.params["product_id"] for lead in leads]
    assert packages == [CABS.package, OLA_PAY.package] * 2
    assert {(lead.surface, lead.request.engine) for lead in leads} == {
        (Surface.PLAY, SerpEngine.GOOGLE_PLAY_PRODUCT)
    }
    assert_answers(leads[0], "play_product")
    assert_answers(leads[2], "play_reviews")


def test_play_reviews_come_in_the_chosen_order() -> None:
    most_relevant = PlayCollector().leads(target(play={"review_sort": "most_relevant"}))
    assert most_relevant[2].request.params["sort_by"] == "1"
    assert len(PlayCollector().leads(target(play={"review_pages": 0}))) == 2  # the rating only


def test_play_is_off_for_a_brand_without_apps() -> None:
    no_apps = Target(OLA, resolve({}))
    assert (PlayCollector().enabled(no_apps), PlayCollector().leads(no_apps)) == (frozenset(), [])
    off = target(play={"enabled": False})
    assert (PlayCollector().enabled(off), PlayCollector().leads(off)) == (frozenset(), [])
    assert PlayCollector().enabled(target()) == {Surface.PLAY}


def test_a_product_page_gives_the_rating_and_a_review_page_the_next_page() -> None:
    product, _, reviews, _ = PlayCollector().leads(target(play={"review_pages": 2}))
    page = PlayCollector().read(product, fixture("play_product"))
    assert page.rating is not None and page.mentions and page.follow_ups == ()
    first = PlayCollector().read(reviews, fixture("play_reviews"))  # as recorded, with its token
    assert first.rating is None
    assert {m.source for m in first.mentions} == {MentionSource.PLAY_REVIEW}
    (second,) = first.follow_ups
    assert (second.page, second.brand_app_id) == (2, CABS.brand_app_id)
    token = fixture("play_reviews")["serpapi_pagination"]["next_page_token"]
    assert second.request.params == {**reviews.request.params, "next_page_token": token}
    assert PlayCollector().read(second, fixture("play_reviews")).follow_ups == ()  # the last


@pytest.mark.parametrize(
    "pagination",
    [
        {},
        {"next_page_token": None},
        {"next_page_token": ""},
        {"next_page_token": "   "},
        {"next_page_token": 7},
        {"next_page_token": "line\nbreak"},
        {"next_page_token": "x" * 2049},
        "x",
        [],
    ],
)
def test_a_missing_or_unusable_page_token_ends_the_reviews(pagination: object) -> None:
    reviews = PlayCollector().leads(target(play={"review_pages": 3}))[2]
    answer = fixture("play_reviews") | {"serpapi_pagination": pagination}
    assert PlayCollector().read(reviews, answer).follow_ups == ()


def test_a_page_token_may_be_as_long_as_the_limit() -> None:
    reviews = PlayCollector().leads(target(play={"review_pages": 3}))[2]
    answer = fixture("play_reviews") | {"serpapi_pagination": {"next_page_token": "x" * 2048}}
    assert len(PlayCollector().read(reviews, answer).follow_ups) == 1


ANSWERS = {
    Surface.SEARCH_PAGE: "google",
    Surface.AUTOCOMPLETE: "autocomplete",
    Surface.NEWS: "news",
    Surface.TRENDS: "trends_timeseries",
    Surface.PLAY: "play_reviews",
}


def chain(collector: Collector, lead: Lead) -> list[Lead]:
    """A lead and every follow-up it leads to, each answered by its surface's recording."""
    leads, waiting = [], [lead]
    while waiting:
        leads.append(current := waiting.pop(0))
        waiting.extend(collector.read(current, fixture(ANSWERS[current.surface])).follow_ups)
    return leads


@pytest.mark.parametrize("cache", [True, False])
def test_skipping_serpapis_cache_reaches_every_collector_and_follow_up(cache: bool) -> None:
    settings = target(serpapi_cache=cache, search_page={"pages": 2}, play={"review_pages": 2})
    leads = [x for c in COLLECTORS for lead in c.leads(settings) for x in chain(c, lead)]
    assert {lead.surface for lead in leads} == set(ANSWERS)
    assert any(lead.page == 2 for lead in leads)  # follow-ups were walked
    assert {lead.request.no_cache for lead in leads} == {not cache}


@pytest.mark.parametrize("name", list(Preset))
@pytest.mark.parametrize("apps", [0, 1, 2])
def test_the_collectors_ask_for_what_the_estimator_counts(name: Preset, apps: int) -> None:
    settings = preset(name)
    owned = (CABS, OLA_PAY)[:apps]
    leads = [lead for c in COLLECTORS for lead in c.leads(Target(OLA, settings, RIVALS, owned))]
    page = settings.search_page
    not_collected_yet = (len(page.templates) if page.enabled and page.ai_overview else 0) + (
        len(settings.youtube.templates) if settings.youtube.enabled else 0
    )
    expected = estimate(settings, BrandFacts(apps=apps, locations=0)) - not_collected_yet
    # Each page is one search; a lead's later pages are its follow-ups.
    assert sum(lead.pages - lead.page + 1 for lead in leads) == expected


@pytest.mark.parametrize("name", list(Preset))
def test_each_surface_has_one_collector_and_leads_stay_within_it(name: Preset) -> None:
    rivals = Target(OLA, preset(name), RIVALS, (CABS,))
    enabled = [collector.enabled(rivals) for collector in COLLECTORS]
    assert sum(len(surfaces) for surfaces in enabled) == len(frozenset().union(*enabled))
    for collector, surfaces in zip(COLLECTORS, enabled, strict=True):
        for lead in collector.leads(rivals):
            assert {lead.surface, *lead.also} <= surfaces

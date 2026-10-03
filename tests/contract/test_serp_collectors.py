"""Collectors: the requests each surface makes for Ola, and what real, redacted answers hold."""

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from serpsense.adapters.serp.collectors import (
    AutocompleteCollector,
    NewsCollector,
    SearchPageCollector,
)
from serpsense.adapters.serp.parsers import parse_autocomplete, parse_news, parse_search_page
from serpsense.domain.enums import SerpEngine, Surface
from serpsense.domain.mention import ParsedMention
from serpsense.domain.settings.search import resolve
from serpsense.ports.collector import Collector, Lead, Subject, Target

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parents[1] / "fixtures" / "serpapi" / "ola"
OLA = Subject(uuid.uuid4(), "Ola")
COMMON = {"gl": "in", "hl": "en", "google_domain": "google.co.in"}
Parse = Callable[[Mapping[str, Any]], list[ParsedMention]]
CASES: list[tuple[Collector, Surface, str, Parse]] = [
    (SearchPageCollector(), Surface.SEARCH_PAGE, "google", parse_search_page),
    (AutocompleteCollector(), Surface.AUTOCOMPLETE, "autocomplete", parse_autocomplete),
    (NewsCollector(), Surface.NEWS, "news", parse_news),
]


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def target(**settings: Any) -> Target:
    return Target(OLA, resolve(settings))


def params(leads: list[Lead]) -> list[dict[str, Any]]:
    return [dict(lead.request.params) for lead in leads]


# Parameters SerpApi echoes with its own defaults when a request leaves them out.
DEFAULTS = frozenset({"device", "hl", "platform", "sort_by", "tz"})


def sent(lead: Lead) -> dict[str, Any]:
    """A request as SerpApi echoes it in `search_parameters`."""
    return {"engine": lead.request.engine.value, **lead.request.params}


def assert_answers(lead: Lead, echoed: dict[str, Any]) -> None:
    """The recorded answer is to exactly this request: everything sent is echoed back, and
    anything else echoed is one of SerpApi's own defaults."""
    assert sent(lead).items() <= echoed.items()
    assert echoed.keys() - sent(lead).keys() <= DEFAULTS


def chain(collector: Collector, lead: Lead, answer: dict[str, Any]) -> list[Lead]:
    """A lead and every follow-up it leads to, each answered with `answer`."""
    leads, waiting = [], [lead]
    while waiting:
        leads.append(current := waiting.pop(0))
        waiting.extend(collector.read(current, answer).follow_ups)
    return leads


def test_each_template_is_searched_in_the_first_language_and_pages_follow() -> None:
    pages = {"templates": ("{brand}", "{brand} reviews"), "pages": 3}
    collector = SearchPageCollector()
    leads = collector.leads(target(search_page=pages, languages=("en", "hi")))
    assert params(leads) == [{**COMMON, "q": "Ola"}, {**COMMON, "q": "Ola reviews"}]
    assert [(lead.surface, lead.page, lead.pages) for lead in leads] == [
        (Surface.SEARCH_PAGE, 1, 3)
    ] * 2
    (second,) = collector.read(leads[0], fixture("google")).follow_ups
    assert (second.page, second.request.params["start"]) == (2, "10")
    (third,) = collector.read(second, fixture("google")).follow_ups
    assert (third.page, third.request.params["start"]) == (3, "20")
    assert collector.read(third, fixture("google")).follow_ups == ()


def test_only_a_first_result_page_can_show_other_surfaces() -> None:
    first = SearchPageCollector().leads(target(search_page={"pages": 2}))[0]
    showing = replace(first, also=frozenset({Surface.AI_OVERVIEW}))
    (second,) = SearchPageCollector().read(showing, fixture("google")).follow_ups
    assert second.also == frozenset()


def test_autocomplete_asks_each_prefix_in_each_language_as_people_type_it() -> None:
    prefixes = {"prefixes": ("{brand} ", "Why is {brand} ")}
    leads = AutocompleteCollector().leads(target(autocomplete=prefixes))
    queries = [(p["q"], p["hl"]) for p in params(leads)]
    assert queries == [("ola ", "en"), ("ola ", "hi"), ("why is ola ", "en"), ("why is ola ", "hi")]
    assert {lead.request.engine for lead in leads} == {SerpEngine.GOOGLE_AUTOCOMPLETE}


def test_news_asks_in_each_language_for_the_brand_and_each_extra_term() -> None:
    news = {"extra_terms": ("complaint",)}
    leads = NewsCollector().leads(target(news=news, languages=("en", "hi")))
    assert [(p["q"], p["hl"]) for p in params(leads)] == [
        ("Ola", "en"),
        ("Ola complaint", "en"),
        ("Ola", "hi"),
        ("Ola complaint", "hi"),
    ]
    assert {lead.request.params["gl"] for lead in leads} == {"in"}


@pytest.mark.parametrize(("collector", "surface", "name", "parse"), CASES)
def test_the_recorded_answers_are_to_the_collectors_own_requests(
    collector: Collector, surface: Surface, name: str, parse: Parse
) -> None:
    lead = collector.leads(target(languages=("en",)))[0]
    assert_answers(lead, fixture(name)["search_parameters"])
    assert lead.surface is surface and surface in collector.enabled(target())
    reading = collector.read(lead, fixture(name))
    assert reading.mentions == tuple(parse(fixture(name)))
    assert len(reading.mentions) > 0


@pytest.mark.parametrize(("collector", "name"), [(c, name) for c, _, name, _ in CASES])
def test_skipping_serpapis_cache_reaches_every_request_and_follow_up(
    collector: Collector, name: str
) -> None:
    for cache, skipped in ((True, False), (False, True)):
        settings = target(serpapi_cache=cache, search_page={"pages": 2})
        leads = [
            x for lead in collector.leads(settings) for x in chain(collector, lead, fixture(name))
        ]
        assert {lead.request.no_cache for lead in leads} == {skipped}


def test_a_surface_that_is_off_asks_for_nothing() -> None:
    off = {"enabled": False}
    settings = target(search_page=off, autocomplete=off, news=off)
    for collector, _, _, _ in CASES:
        assert (collector.enabled(settings), list(collector.leads(settings))) == (frozenset(), [])


def test_a_brand_name_is_searched_as_it_is_written() -> None:
    braces = Target(Subject(uuid.uuid4(), "  {brand}\n& Co "), resolve({}))
    assert params(NewsCollector().leads(braces))[0]["q"] == "{brand} & Co"
    assert params(SearchPageCollector().leads(braces))[0]["q"] == "{brand} & Co"

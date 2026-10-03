"""Collectors: the searches each surface makes, read by the parsers (ADR-0007, BUILD_PLAN §10).

Each collector asks for the searches `domain.estimator` counts for it, so the budget check
before a scan holds; the estimator also counts the AI Overview follow-up, which comes with its
collector. The search page is searched in the brand's first language and news in every
language, as the estimator counts them. Maps and YouTube follow once responses showing them are
recorded.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from serpsense.adapters.serp.parsers import (
    parse_autocomplete,
    parse_news,
    parse_play_product,
    parse_search_page,
    parse_trends_related,
    parse_trends_timeseries,
)
from serpsense.domain.enums import SerpEngine, Surface
from serpsense.domain.observation import TrendsPoint
from serpsense.domain.search import InvalidSearchParams, ParamValue
from serpsense.domain.settings.search import ReviewSort, SearchSettings
from serpsense.ports.collector import Collector, Lead, Reading, Target
from serpsense.ports.search_provider import SearchRequest

RESULTS_PER_PAGE = 10  # Google's page size; `start` skips whole pages
REVIEW_SORT = {ReviewSort.MOST_RELEVANT: "1", ReviewSort.NEWEST: "2"}  # SerpApi's `sort_by`
MAX_PAGE_TOKEN = 2048


def request(
    settings: SearchSettings, engine: SerpEngine, params: Mapping[str, ParamValue]
) -> SearchRequest:
    """A request with the scan's choice about SerpApi's own cache."""
    return SearchRequest(engine, params, no_cache=not settings.serpapi_cache)


def _on(surface: Surface, enabled: bool) -> frozenset[Surface]:
    return frozenset({surface}) if enabled else frozenset()


class SearchPageCollector:
    """Organic results and People also ask for each template; each page leads to the next."""

    def enabled(self, target: Target) -> frozenset[Surface]:
        return _on(Surface.SEARCH_PAGE, target.settings.search_page.enabled)

    def leads(self, target: Target) -> list[Lead]:
        settings, page = target.settings, target.settings.search_page
        if not self.enabled(target):
            return []
        common = {
            "gl": settings.country,
            "hl": settings.languages[0],
            "google_domain": settings.google_domain,
        }
        return [
            Lead(
                Surface.SEARCH_PAGE,
                request(
                    settings,
                    SerpEngine.GOOGLE,
                    {**common, "q": template.format(brand=target.brand.name)},
                ),
                pages=page.pages,
            )
            for template in page.templates
        ]

    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        return Reading(
            mentions=tuple(parse_search_page(payload)), follow_ups=_next_result_page(lead)
        )


def _next_result_page(lead: Lead) -> tuple[Lead, ...]:
    """The same search one page further, while pages are left. Only a first page can show the
    other surfaces (the AI Overview), so a later page names none."""
    if lead.page >= lead.pages:
        return ()
    params = {**lead.request.params, "start": lead.page * RESULTS_PER_PAGE}
    following = SearchRequest(lead.request.engine, params, no_cache=lead.request.no_cache)
    return (replace(lead, request=following, page=lead.page + 1, also=frozenset()),)


class AutocompleteCollector:
    """Google's suggestions for each prefix, in each language, typed in lower case as people
    type them (suggestions come back in lower case either way)."""

    def enabled(self, target: Target) -> frozenset[Surface]:
        return _on(Surface.AUTOCOMPLETE, target.settings.autocomplete.enabled)

    def leads(self, target: Target) -> list[Lead]:
        settings = target.settings
        if not self.enabled(target):
            return []
        return [
            Lead(
                Surface.AUTOCOMPLETE,
                request(
                    settings,
                    SerpEngine.GOOGLE_AUTOCOMPLETE,
                    {
                        "q": prefix.format(brand=target.brand.name).lower(),
                        "gl": settings.country,
                        "hl": language,
                    },
                ),
            )
            for prefix in settings.autocomplete.prefixes
            for language in settings.languages
        ]

    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        return Reading(mentions=tuple(parse_autocomplete(payload)))


class NewsCollector:
    """Google News for the brand, and for the brand with each extra term, in each language."""

    def enabled(self, target: Target) -> frozenset[Surface]:
        return _on(Surface.NEWS, target.settings.news.enabled)

    def leads(self, target: Target) -> list[Lead]:
        settings, name = target.settings, target.brand.name
        if not self.enabled(target):
            return []
        queries = [name, *(f"{name} {term}" for term in settings.news.extra_terms)]
        return [
            Lead(
                Surface.NEWS,
                request(
                    settings,
                    SerpEngine.GOOGLE_NEWS,
                    {"q": query, "gl": settings.country, "hl": language},
                ),
            )
            for language in settings.languages
            for query in queries
        ]

    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        return Reading(mentions=tuple(parse_news(payload)))


class TrendsCollector:
    """One joint query comparing the brand with its competitors, so their interest is on one
    scale, and the brand's own related queries (ADR-0007). Trends separates queries with commas:
    a competitor whose name holds one is left out, and a brand whose name holds one isn't
    searched on Trends at all."""

    def enabled(self, target: Target) -> frozenset[Surface]:
        on = target.settings.trends.enabled and "," not in target.brand.name
        return _on(Surface.TRENDS, on)

    def leads(self, target: Target) -> list[Lead]:
        settings, trends = target.settings, target.settings.trends
        if not self.enabled(target):
            return []
        compared = [target.brand, *(c for c in target.competitors if "," not in c.name)]
        window = {"geo": trends.region, "date": trends.date_range}
        over_time = {"q": ",".join(s.name for s in compared), "data_type": "TIMESERIES"}
        leads = [
            Lead(
                Surface.TRENDS,
                request(settings, SerpEngine.GOOGLE_TRENDS, {**over_time, **window}),
                subjects=tuple(subject.brand_id for subject in compared),
            )
        ]
        if trends.related_queries:
            related = {"q": target.brand.name, "data_type": "RELATED_QUERIES", **window}
            leads.append(Lead(Surface.TRENDS, request(settings, SerpEngine.GOOGLE_TRENDS, related)))
        return leads

    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        if not lead.subjects:
            return Reading(mentions=tuple(parse_trends_related(payload)))
        points = parse_trends_timeseries(payload)
        return Reading(trends=_one_per_moment(points, lines=len(lead.subjects)))


class PlayCollector:
    """Each app's product page (its rating and its "most relevant" reviews), then `review_pages`
    pages of its reviews in the chosen order; each page's answer leads to the next. Product pages
    come first, so a scan cut short still has every app's rating. A brand without apps has
    nothing to collect here, as if Play were off."""

    def enabled(self, target: Target) -> frozenset[Surface]:
        return _on(Surface.PLAY, target.settings.play.enabled and bool(target.apps))

    def leads(self, target: Target) -> list[Lead]:
        settings, play = target.settings, target.settings.play
        if not self.enabled(target):
            return []
        products = {
            app.brand_app_id: {
                "product_id": app.package,
                "store": "apps",
                "gl": settings.country,
                "hl": settings.languages[0],
            }
            for app in target.apps
        }
        engine = SerpEngine.GOOGLE_PLAY_PRODUCT
        leads = [
            Lead(Surface.PLAY, request(settings, engine, page), brand_app_id=app)
            for app, page in products.items()
        ]
        if play.review_pages:
            order = {"all_reviews": "true", "sort_by": REVIEW_SORT[play.review_sort]}
            leads += [
                Lead(
                    Surface.PLAY,
                    request(settings, engine, {**page, **order}),
                    brand_app_id=app,
                    pages=play.review_pages,
                )
                for app, page in products.items()
            ]
        return leads

    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        rating, reviews = parse_play_product(payload)
        return Reading(
            mentions=tuple(reviews), rating=rating, follow_ups=_next_review_page(lead, payload)
        )


COLLECTORS: tuple[Collector, ...] = (
    SearchPageCollector(),
    AutocompleteCollector(),
    NewsCollector(),
    TrendsCollector(),
    PlayCollector(),
)


def _one_per_moment(points: Iterable[TrendsPoint], *, lines: int) -> tuple[TrendsPoint, ...]:
    """Points on the comparison's lines, one for each line and time: the first given."""
    kept: dict[tuple[int, datetime], TrendsPoint] = {}
    for point in points:
        if point.query_index < lines:
            kept.setdefault((point.query_index, point.observed_at), point)
    return tuple(kept.values())


def _next_review_page(lead: Lead, payload: Mapping[str, Any]) -> tuple[Lead, ...]:
    """The same request for the next page, while pages are left and the answer gives a token."""
    pagination = payload.get("serpapi_pagination")
    token = pagination.get("next_page_token") if isinstance(pagination, Mapping) else None
    if lead.page >= lead.pages or not isinstance(token, str):
        return ()
    if not token.strip() or len(token) > MAX_PAGE_TOKEN:
        return ()
    params = {**lead.request.params, "next_page_token": token}
    try:
        following = SearchRequest(lead.request.engine, params, no_cache=lead.request.no_cache)
    except InvalidSearchParams:  # a token with control characters, or shaped like a key
        return ()
    return (replace(lead, request=following, page=lead.page + 1),)

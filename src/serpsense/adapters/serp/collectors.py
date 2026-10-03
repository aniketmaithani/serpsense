"""Collectors: the searches each surface makes, read by the parsers (ADR-0007, BUILD_PLAN §10).

Each collector asks for the searches `domain.estimator` counts for it, so the budget check
before a scan holds; the estimator also counts the AI Overview follow-up, which comes with its
collector. The search page is searched in the brand's first language and news in every
language, as the estimator counts them. Maps and YouTube follow once responses showing them are
recorded.
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from serpsense.adapters.serp.parsers import parse_autocomplete, parse_news, parse_search_page
from serpsense.domain.enums import SerpEngine, Surface
from serpsense.domain.search import ParamValue
from serpsense.domain.settings.search import SearchSettings
from serpsense.ports.collector import Lead, Reading, Target
from serpsense.ports.search_provider import SearchRequest

RESULTS_PER_PAGE = 10  # Google's page size; `start` skips whole pages


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

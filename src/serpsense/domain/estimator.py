"""How many SerpApi searches a scan may make (BUILD_PLAN §6.3), before it runs.

An upper bound: the AI Overview follow-up is counted for every search-page template although
Google only sometimes needs it, and cached results (free) are counted as live.
"""

from dataclasses import dataclass

from serpsense.domain.settings.search import SearchSettings


@dataclass(frozen=True)
class BrandFacts:
    apps: int  # Play apps (brand_apps)
    locations: int  # Maps places (brand_locations)
    unresolved_locations: int = 0  # places not yet resolved to a Maps id: one lookup each


def estimate(settings: SearchSettings, brand: BrandFacts) -> int:
    languages = len(settings.languages)
    page, news, maps = settings.search_page, settings.news, settings.maps
    total = 0
    if page.enabled:
        total += len(page.templates) * page.pages + (len(page.templates) if page.ai_overview else 0)
    if settings.autocomplete.enabled:
        total += len(settings.autocomplete.prefixes) * languages
    if news.enabled:
        total += languages * (1 + len(news.extra_terms))
    if settings.trends.enabled:  # one joint query with all competitors, plus related queries
        total += 1 + int(settings.trends.related_queries)
    if settings.play.enabled:
        total += brand.apps * (1 + settings.play.review_pages)  # the product page, then reviews
    if maps.enabled:
        total += brand.locations * maps.review_pages + brand.unresolved_locations
    if settings.youtube.enabled:
        total += len(settings.youtube.templates)
    return total

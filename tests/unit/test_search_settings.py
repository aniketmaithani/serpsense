"""Search settings documents, their layering, presets and the estimate (BUILD_PLAN §6)."""

from typing import Any

import pytest
from pydantic import ValidationError

from serpsense.domain.estimator import BrandFacts, estimate
from serpsense.domain.settings.search import (
    Preset,
    ReviewSort,
    SearchSettings,
    for_brand,
    merge,
    preset,
    resolve,
)

pytestmark = pytest.mark.unit

ONE_EACH = BrandFacts(apps=1, locations=1)
SURFACES = ("search_page", "autocomplete", "news", "trends", "play", "maps", "youtube")


def test_defaults_are_the_plans_and_frozen() -> None:
    settings = SearchSettings()
    assert (settings.country, settings.languages, settings.max_searches) == ("in", ("en", "hi"), 25)
    assert settings.autocomplete.prefixes == ("{brand} ", "{brand} is ", "is {brand} ")
    assert (settings.play.review_sort, settings.maps.enabled) == (ReviewSort.NEWEST, True)
    assert settings == preset(Preset.STANDARD)
    with pytest.raises(ValidationError):
        settings.country = "us"  # type: ignore[misc]  # proving it is frozen


def test_the_snapshot_is_plain_json_and_reads_back_the_same() -> None:
    settings = preset(Preset.DEEP)
    assert SearchSettings.model_validate(settings.model_dump(mode="json")) == settings


def test_layers_merge_per_surface_and_later_layers_win() -> None:
    user = {"languages": ["en", "hi"], "search_page": {"pages": 2}}
    brand = {
        "search_page": {"templates": ["{brand}", "{brand} reviews"]},
        "maps": {"enabled": False},
    }
    run = {"languages": ["hi"]}
    settings = resolve({}, user, brand, run)
    assert settings.languages == ("hi",)
    assert (settings.search_page.pages, settings.search_page.templates) == (
        2,
        ("{brand}", "{brand} reviews"),
    )
    assert not settings.maps.enabled and settings.search_page.ai_overview  # defaults stay


@pytest.mark.parametrize(
    "document",
    [
        {"search_page": {"templates": ["Ola only"]}},  # a template must name {brand}
        {"search_page": {"templates": ["{brand} {city}"]}},  # and nothing else
        {"search_page": {"templates": ["{brand.__class__}"]}},
        {"search_page": {"templates": ["{brand!r}"]}},
        {"search_page": {"templates": ["{brand:>20}"]}},
        {"search_page": {"templates": ["{{brand}}"]}},  # an escaped brace names nothing
        {"search_page": {"templates": ["{brand"]}},
        {"search_page": {"templates": ["{brand}\x00"]}},  # jsonb can't hold NUL
        {"search_page": {"templates": ["{brand} " + "x" * 100]}},
        {"search_page": {"templates": ["{brand}", "{brand}"]}},
        {"search_page": {"pages": 4}},
        {"autocomplete": {"prefixes": []}},
        {"news": {"extra_terms": [" "]}},
        {"news": {"extra_terms": ["refund", "refund"]}},
        {"news": {"extar_terms": ["refund"]}},  # a typo is refused, not ignored
        {"languages": ["EN"]},
        {"languages": ["en", "en"]},
        {"trends": {"region": "india"}},
        {"trends": {"date_range": "now 99-H"}},  # only the ranges Google offers
        {"play": {"review_sort": "rating"}},
        {"maps": {"review_pages": 0}},
        {"max_searches": 0},
        {"country": "IND"},
    ],
)
def test_invalid_documents_are_refused(document: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        resolve(document)


@pytest.mark.parametrize(
    ("name", "searches"), [(Preset.LEAN, 8), (Preset.STANDARD, 15), (Preset.DEEP, 24)]
)
def test_presets_estimate_and_fit_the_scan_limit(name: Preset, searches: int) -> None:
    """BUILD_PLAN §10 with one app and one place: Lean ≈ 8, Standard ≈ 15."""
    settings = preset(name)
    assert estimate(settings, ONE_EACH) == searches <= settings.max_searches


def test_the_free_plan_demo_settings() -> None:
    """BUILD_PLAN §22: Ola every 12h on 8 searches; each competitor daily on 3, rating only."""
    off = {"enabled": False}
    ola = {
        "languages": ["en"],
        "autocomplete": {"prefixes": ["{brand} "]},
        "maps": off,
    }
    competitor = {
        "languages": ["en"],
        "search_page": {"ai_overview": False},
        "autocomplete": off,
        "trends": off,
        "play": {"review_pages": 0},
        "maps": off,
    }
    assert estimate(resolve(ola), BrandFacts(apps=1, locations=0)) == 8
    assert estimate(resolve(competitor), BrandFacts(apps=1, locations=0)) == 3


@pytest.mark.parametrize(
    ("play", "calls"),
    [
        ({"review_pages": 0}, 1),  # the product page: rating only
        ({"review_pages": 2}, 3),  # plus two pages of newest reviews
        ({"review_sort": "most_relevant", "review_pages": 1}, 2),  # whatever the order
    ],
)
def test_play_counts_the_product_page_and_review_pages(play: dict[str, Any], calls: int) -> None:
    only_play = {key: {"enabled": False} for key in SURFACES if key != "play"}
    assert (
        estimate(resolve(only_play, {"play": play}), BrandFacts(apps=2, locations=0)) == 2 * calls
    )


def test_the_estimate_counts_every_surface() -> None:
    everything = preset(Preset.DEEP)
    facts = BrandFacts(apps=2, locations=3, unresolved_locations=1)
    # search page 3 templates + 3 AI Overview follow-ups; autocomplete 3 x 2 languages;
    # news 2 languages x (1 + 1 term); Trends 2; Play 2 apps x (product page + 2 pages);
    # Maps 3 places x 2 pages + 1 lookup; YouTube 1.
    assert estimate(everything, facts) == 6 + 6 + 4 + 2 + 6 + 7 + 1
    nothing = {key: {"enabled": False} for key in SURFACES}
    assert estimate(resolve(nothing), facts) == 0


def test_a_brands_own_languages_win_over_every_document() -> None:
    defaults, brand = (
        {"languages": ["en"], "news": {"enabled": False}},
        {"news": {"extra_terms": ["x"]}},
    )
    assert for_brand(defaults, brand, ("hi", "en")).languages == ("hi", "en")
    assert for_brand(defaults, brand, ()).languages == ("en",)
    assert merge(defaults, brand)["news"] == {"enabled": False, "extra_terms": ["x"]}

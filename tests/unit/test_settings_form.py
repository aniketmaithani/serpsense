"""The settings form's sections and hidden inputs: every knob is in one section, and what the
form posts for the sections not shown reads back as the same knobs."""

from dataclasses import fields, replace
from typing import Any

import pytest

from serpsense.domain.settings.search import ReviewSort, SearchSettings
from serpsense.entrypoints.web.settings_form import (
    SECTIONS,
    Section,
    SettingsForm,
    hidden,
    section_of,
)
from serpsense.services.brand_settings import Knobs

pytestmark = pytest.mark.unit

KNOBS = replace(
    Knobs.of(SearchSettings(), 720),
    languages=("en", "ta"),
    news_terms=(),  # nothing posted at all
    autocomplete_prefixes=("{brand} ", "is {brand} "),  # trailing spaces kept
    maps=False,
    maps_review_sort=ReviewSort.MOST_RELEVANT,
)
LISTS = {"languages", "search_templates", "autocomplete_prefixes", "news_terms"}
LISTS |= {"youtube_templates"}


def test_every_knob_is_in_exactly_one_section() -> None:
    named = [name for _, names in SECTIONS.values() for name in names]
    assert sorted(named) == sorted(f.name for f in fields(Knobs))
    assert list(SECTIONS) == list(Section)


@pytest.mark.parametrize("shown", list(Section))
def test_the_hidden_inputs_and_the_shown_section_post_the_same_knobs(shown: Section) -> None:
    other = next(section for section in Section if section is not shown)
    pairs = hidden(KNOBS, shown) + [
        (name, value) for name, value in hidden(KNOBS, other) if name in SECTIONS[shown][1]
    ]
    posted: dict[str, Any] = {}
    for name, value in pairs:
        if name in LISTS:
            posted.setdefault(name, []).append(value)
        else:
            assert name not in posted, name  # one input each
            posted[name] = value
    assert SettingsForm.model_validate(posted).knobs() == KNOBS


def test_a_section_link_names_one_or_shows_the_first() -> None:
    assert section_of("trends") is Section.TRENDS
    assert section_of("") is section_of("<b>") is Section.GENERAL


def test_textareas_and_languages_parse_once() -> None:
    form = SettingsForm.model_validate(
        {
            **{name: value for name, value in hidden(KNOBS, Section.GENERAL)},
            "max_searches": "5",
            "country": " in ",
            "google_domain": "google.co.in",
            "languages": "EN, hi  ta,,",
            "search_templates": "{brand}\r\n\r\n  \r\n{brand} reviews",
            "autocomplete_prefixes": ["{brand} "],
            "youtube_templates": ["{brand}"],
        }
    )
    assert form.languages == ("en", "hi", "ta") and form.country == "in"
    assert form.search_templates == ("{brand}", "{brand} reviews")
    assert form.news_terms == () and form.interval_minutes is None

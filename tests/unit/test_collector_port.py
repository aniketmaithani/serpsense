"""What a collector is given and gives back: targets and leads that can't be built wrong."""

import uuid

import pytest

from serpsense.domain.enums import SerpEngine, Surface
from serpsense.domain.settings.search import resolve
from serpsense.ports.collector import App, Lead, Subject, Target
from serpsense.ports.search_provider import SearchRequest

pytestmark = pytest.mark.unit

OLA = Subject(uuid.uuid4(), "Ola")
REQUEST = SearchRequest(SerpEngine.GOOGLE, {"q": "Ola"})


def rival(name: str) -> Subject:
    return Subject(uuid.uuid4(), name)


def test_a_target_has_at_most_four_distinct_competitors_and_each_app_once() -> None:
    four = tuple(rival(name) for name in ("Uber", "Rapido", "Namma Yatri", "inDrive"))
    assert len(Target(OLA, resolve({}), competitors=four).competitors) == 4
    with pytest.raises(ValueError, match="at most four"):
        Target(OLA, resolve({}), competitors=(*four, rival("BluSmart")))
    with pytest.raises(ValueError, match="once"):
        Target(OLA, resolve({}), competitors=(four[0], four[0]))
    with pytest.raises(ValueError, match="once"):
        Target(OLA, resolve({}), competitors=(OLA,))
    cabs = App(uuid.uuid4(), "com.olacabs.customer")
    with pytest.raises(ValueError, match="each app once"):
        Target(OLA, resolve({}), apps=(cabs, cabs))


def test_a_brand_name_is_searched_with_its_whitespace_collapsed() -> None:
    assert rival("  Namma \n Yatri ").name == "Namma Yatri"


@pytest.mark.parametrize(
    "name", [" \n", "", "\u200b", "Ola\x00", "O\u200bla", "Ola\ud800", "Ola\u202e"]
)
def test_a_brand_name_has_visible_characters_only(name: str) -> None:
    with pytest.raises(ValueError, match="brand"):
        rival(name)


@pytest.mark.parametrize("package", ["", "com.ola cabs", "x" * 256, "com.ola\x01app"])
def test_a_store_id_has_no_whitespace_or_control_characters(package: str) -> None:
    with pytest.raises(ValueError, match="store id"):
        App(uuid.uuid4(), package)


@pytest.mark.parametrize("value", ["Ola api_key=abc", "x" * 40 + "api_key=" + "a" * 64])
def test_a_name_or_store_id_that_looks_like_a_key_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="can't be sent"):
        rival(value)
    with pytest.raises(ValueError, match="can't be sent"):
        App(uuid.uuid4(), value.replace(" ", ""))


@pytest.mark.parametrize(("page", "pages"), [(0, 1), (2, 1), (1, 0)])
def test_a_lead_is_one_of_its_pages(page: int, pages: int) -> None:
    with pytest.raises(ValueError, match="page"):
        Lead(Surface.SEARCH_PAGE, REQUEST, page=page, pages=pages)


def test_a_lead_also_names_only_other_surfaces() -> None:
    with pytest.raises(ValueError, match="other surfaces"):
        Lead(Surface.SEARCH_PAGE, REQUEST, also=frozenset({Surface.SEARCH_PAGE}))
    also = frozenset({Surface.AI_OVERVIEW})
    assert Lead(Surface.SEARCH_PAGE, REQUEST, also=also).also == also


def test_a_brand_name_may_hold_the_joiners_indic_spellings_need() -> None:
    assert rival("\u0915\u094d\u200d\u0937").name == "\u0915\u094d\u200d\u0937"

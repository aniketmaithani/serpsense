"""Surface scores and health on fixed inputs, worked by hand (BUILD_PLAN §11, docs/scoring.md)."""

import pytest

from serpsense.domain.enums import Surface
from serpsense.domain.scoring.surfaces import (
    Seen,
    ai_overview,
    autocomplete,
    autocomplete_penalty,
    half_up,
    health,
    news,
    ratio,
    reviews,
    search_page,
)

pytestmark = pytest.mark.unit

NEG, NEU, POS = -1, 0, 1


@pytest.mark.parametrize(
    ("results", "score"),
    [
        ([], None),
        # weights 1, 1/log2(3) = 0.631 and 1/log2(4) = 0.5: negative share 1/2.131 = 47%.
        ([Seen(NEG, 1), Seen(POS, 2), Seen(NEU, 3)], 53),
        ([Seen(POS, 1), Seen(POS, 2)], 100),
        ([Seen(NEG, None)], 0),  # unranked weighs like the tenth result
        ([Seen(POS, 1), Seen(NEG, 9)], 77),  # low down, a negative result costs less
    ],
)
def test_search_page(results: list[Seen], score: int | None) -> None:
    assert search_page(results) == score


@pytest.mark.parametrize(
    ("suggestions", "score"),
    [
        ([], None),
        ([Seen(POS, 1), Seen(NEU, 2)], 100),
        ([Seen(NEG, 1), Seen(NEU, 2), Seen(NEG, 5)], 25),  # 45 for the top one, 30 lower down
        ([Seen(NEG, 1), Seen(NEG, 2), Seen(NEG, 3)], 0),  # 45 + 40 + 35, floored at 0
    ],
)
def test_autocomplete(suggestions: list[Seen], score: int | None) -> None:
    assert autocomplete(suggestions) == score


def test_ai_overview_maps_its_sentiment() -> None:
    assert [ai_overview(None), *(ai_overview(Seen(s)) for s in (NEG, NEU, POS))] == [
        None,
        0,
        50,
        100,
    ]


def test_news_counts_half_the_neutral_and_rounds_half_up() -> None:
    assert news([]) is None
    assert news([Seen(POS), Seen(POS), Seen(NEU), Seen(NEG)]) == 63  # 62.5, not banker's 62
    assert half_up(62.5) == 63 and half_up(62.4) == 62


def test_reviews_halve_the_rating_and_the_newest_sentiment() -> None:
    newest = [Seen(POS), Seen(NEG), Seen(NEG), Seen(POS), Seen(NEU)]  # sentiment 0 → 50
    assert reviews(460, newest) == 70  # (90 + 50) / 2
    assert (reviews(460, []), reviews(None, newest), reviews(None, [])) == (90, 50, None)
    assert reviews(None, [Seen(NEG)] * 50 + [Seen(POS)] * 10) == 0  # only the newest 50 count


def test_health_spreads_weights_over_the_surfaces_seen() -> None:
    scores = {
        Surface.SEARCH_PAGE: 53,
        Surface.AUTOCOMPLETE: 25,
        Surface.AI_OVERVIEW: None,  # not shown: its weight goes to the others
        Surface.NEWS: 63,
        Surface.PLAY: 70,
        Surface.TRENDS: 80,  # not a health surface
    }
    # (2500·53 + 2000·25 + 1500·63 + 1500·70) / 7500 = 50.93
    assert health(scores) == 51
    assert health({Surface.MAPS: None}) is None


def test_penalties_and_exact_halves() -> None:
    assert [autocomplete_penalty(r) for r in (1, 2, 3, 4, 9, None)] == [45, 40, 35, 30, 30, 30]
    assert autocomplete([Seen(-1, 2), Seen(-1, 3)]) == 25  # 100 - 40 - 35, no floor involved
    assert health({Surface.SEARCH_PAGE: 51, Surface.AUTOCOMPLETE: 50, Surface.NEWS: 50}) == 50
    assert ratio(1, 200) == 1 and ratio(1, 201) == 0  # exactly .5 rounds up, below doesn't


@pytest.mark.parametrize(
    "bad",
    [
        lambda: Seen(2),
        lambda: Seen(1, 0),
        lambda: reviews(600, []),
        lambda: health({Surface.NEWS: 150}),
    ],
)
def test_out_of_range_inputs_are_refused(bad: object) -> None:
    with pytest.raises(ValueError):
        bad()  # type: ignore[operator]  # a lambda

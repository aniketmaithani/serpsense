"""One scan's scores from what it saw: surface scores and crisis components (docs/scoring.md)."""

from datetime import UTC, datetime, timedelta

import pytest

from serpsense.domain.enums import CrisisComponent, MentionSource, Surface
from serpsense.domain.scoring import crisis as rules
from serpsense.domain.scoring import surfaces
from serpsense.domain.scoring.scan import Earlier, Observed, ScoreInputs, score
from serpsense.domain.scoring.surfaces import Seen

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
M, C = MentionSource, CrisisComponent
EVERY = frozenset(Surface)  # every surface collected before: nothing is the baseline


def news(sentiment: int, *, hours: int = 1, new: bool = False, severity: int = 50) -> Observed:
    at = AT - timedelta(hours=hours)
    return Observed(M.NEWS, sentiment, severity, published_at=at, new=new)


def test_each_surface_scores_what_it_showed() -> None:
    seen = (
        Observed(M.SERP_RESULT, -1, position=1),
        Observed(M.TOP_STORY, 1, position=2),
        Observed(M.PEOPLE_ALSO_ASK, -1, position=1),  # a question, not a result
        news(1),
        news(0, hours=-2),  # published after the scan was made, before it collected
        news(-1, hours=8 * 24),  # older than a week
    )
    scores = score(ScoreInputs(AT, seen, ratings=(410, 390), newest_reviews=(-1, 1, 1)))
    assert scores.surfaces == {
        Surface.SEARCH_PAGE: surfaces.search_page([Seen(-1, 1), Seen(1, 2)]),
        Surface.NEWS: 75,  # this week's two articles: one positive, one neutral
        Surface.PLAY: surfaces.reviews(400, [Seen(-1), Seen(1), Seen(1)]),  # ratings averaged
    }  # autocomplete and the AI Overview showed nothing, so they score nothing
    assert set(scores.components) == set(CrisisComponent)


def test_a_surfaces_first_collection_is_its_baseline_not_a_crisis() -> None:
    first = tuple(news(-1, new=True, severity=90) for _ in range(9))
    assert set(score(ScoreInputs(AT, first)).components.values()) == {0}  # never collected
    collected = score(ScoreInputs(AT, first, collected_before=EVERY)).components
    assert collected[C.VELOCITY] == rules.velocity(9, []) > 0
    assert collected[C.PRESS] == 100


def test_velocity_and_spread_compare_with_the_brands_usual() -> None:
    usual = Earlier({Surface.NEWS: 2, Surface.SEARCH_PAGE: 1}, collected_before=EVERY)
    baseline = Earlier({Surface.NEWS: 50}, collected_before=frozenset())  # its own baseline
    seen = (*(news(-1, new=True, hours=72) for _ in range(6)), Observed(M.SERP_RESULT, 1, new=True))
    components = score(
        ScoreInputs(AT, seen, earlier=(usual, baseline, baseline), collected_before=EVERY)
    ).components
    assert components[C.VELOCITY] == rules.velocity(6, [3, 0, 0]) == 67  # the usual is 2
    surfaces_seen = [rules.SurfaceNegatives(6, [2, 0, 0]), rules.SurfaceNegatives(0, [1, 0, 0])]
    assert components[C.SPREAD] == rules.spread(surfaces_seen) == 50  # news did, results didn't
    assert components[C.PRESS] == 0  # published three days ago, not in the last 48 hours


def test_search_visible_signals_count_only_when_new_and_negative() -> None:
    seen = (
        Observed(M.AUTOCOMPLETE, -1, position=2, new=True),
        Observed(M.AUTOCOMPLETE, -1, position=1),  # seen before: not new
        Observed(M.TRENDS_QUERY, -1, position=3, new=True),  # rising
        Observed(M.TRENDS_QUERY, -1, position=30, new=True),  # a top query, not rising
        Observed(M.TRENDS_QUERY, 1, position=4, new=True),  # not negative
    )
    components = score(ScoreInputs(AT, seen, collected_before=EVERY)).components
    assert components[C.AUTOCOMPLETE] == rules.new_negative_autocomplete([2]) == 90
    assert components[C.TRENDS] == rules.rising_negative_trends(1) == 60

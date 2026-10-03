"""The crisis score and level on fixed inputs, worked by hand (BUILD_PLAN §11, docs/scoring.md)."""

import pytest

from serpsense.domain.enums import CrisisComponent, CrisisLevel
from serpsense.domain.scoring.crisis import (
    SurfaceNegatives,
    crisis,
    has_level,
    level,
    new_negative_autocomplete,
    press,
    rising_negative_trends,
    spread,
    usual,
    velocity,
)

pytestmark = pytest.mark.unit


def test_the_usual_is_the_median_of_the_newest_eight_never_below_two() -> None:
    assert (usual([]), usual([0, 1]), usual([5, 9, 1]), usual([10, 4])) == (2, 2, 5, 4)
    assert usual([3] * 8 + [99, 99, 99]) == 3  # only the newest eight count


@pytest.mark.parametrize(
    ("new", "earlier", "score"),
    [
        (2, [], 0),  # the usual is at least 2
        (8, [2, 2], 100),  # four times the usual
        (5, [1, 3], 50),
        (20, [10], 33),
        (14, [11] * 13 + [17], 9),  # exact integers: 3/33 is 9.09, no float drift
        (1, [6], 0),  # quieter than usual
    ],
)
def test_velocity_compares_new_negatives_with_the_usual(
    new: int, earlier: list[int], score: int
) -> None:
    assert velocity(new, earlier) == score


def test_a_typical_scan_of_a_busy_brand_is_no_crisis() -> None:
    """Ola: a few new negative reviews and articles each scan is its usual, not a crisis."""
    usual_week = [4, 5, 3, 4, 6, 4, 5, 4]
    surfaces = [
        SurfaceNegatives(4, usual_week),
        SurfaceNegatives(2, [2, 3, 2]),
        SurfaceNegatives(0),
    ]
    components = {
        CrisisComponent.VELOCITY: velocity(5, usual_week),
        CrisisComponent.SPREAD: spread(surfaces),
        CrisisComponent.PRESS: press([30]),  # one new negative article
    }
    # One more new negative than usual (5 against 4) barely moves velocity.
    assert components == {
        CrisisComponent.VELOCITY: 8,
        CrisisComponent.SPREAD: 0,
        CrisisComponent.PRESS: 15,
    }
    assert crisis(components) == 4  # (3000 * 8 + 1000 * 15) / 10000
    assert level(crisis(components)) is CrisisLevel.LOW


def test_the_other_crisis_components() -> None:
    surfaces = [SurfaceNegatives(9, [2, 3]), SurfaceNegatives(1), SurfaceNegatives(3, [1])]
    assert (spread(surfaces), spread([])) == (67, 0)  # two of three above their usual
    assert [new_negative_autocomplete(r) for r in ([], [1], [2, 5], [None], [7])] == [
        0,
        100,
        90,
        70,
        70,
    ]
    assert [rising_negative_trends(n) for n in (0, 1, 2, 5)] == [0, 60, 80, 100]
    assert (press([]), press([60, 70]), press([90, 90, 90]), press([1])) == (0, 65, 100, 1)


def test_crisis_and_its_level() -> None:
    components = {
        CrisisComponent.VELOCITY: 100,
        CrisisComponent.SPREAD: 50,
        CrisisComponent.AUTOCOMPLETE: 90,
        CrisisComponent.TRENDS: 60,
        CrisisComponent.PRESS: 65,
    }
    assert crisis(components) == 76  # (300000 + 125000 + 180000 + 90000 + 65000) / 10000
    assert crisis({CrisisComponent.VELOCITY: 50}) == 15  # a missing component counts as 0
    assert crisis({CrisisComponent.PRESS: 5}) == 1  # 0.5 rounds up
    assert [level(s) for s in (0, 39, 40, 69, 70, 100)] == [
        CrisisLevel.LOW,
        CrisisLevel.LOW,
        CrisisLevel.MEDIUM,
        CrisisLevel.MEDIUM,
        CrisisLevel.HIGH,
        CrisisLevel.HIGH,
    ]
    assert CrisisLevel.HIGH.rank > CrisisLevel.MEDIUM.rank > CrisisLevel.LOW.rank


@pytest.mark.parametrize(
    "bad",
    [
        lambda: press([-50]),
        lambda: new_negative_autocomplete([-5]),
        lambda: crisis({CrisisComponent.SPREAD: 167}),
        lambda: level(-1),
    ],
)
def test_out_of_range_inputs_are_refused(bad: object) -> None:
    with pytest.raises(ValueError):
        bad()  # type: ignore[operator]  # a lambda


def test_a_brand_has_no_crisis_level_until_it_has_three_earlier_scans() -> None:
    assert [has_level(n) for n in (0, 1, 2, 3, 10)] == [False, False, False, True, True]

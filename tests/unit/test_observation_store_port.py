"""A Trends comparison is checked before it reaches the database (data-model §5)."""

import uuid
from datetime import UTC, datetime

import pytest

from serpsense.domain.observation import TrendsPoint
from serpsense.ports.observation_store import Comparison

pytestmark = pytest.mark.unit

SCAN, NOW = uuid.uuid4(), datetime(2026, 10, 3, tzinfo=UTC)
OLA, UBER = uuid.uuid4(), uuid.uuid4()


def point(index: int) -> TrendsPoint:
    return TrendsPoint("Ola", index, NOW, 50, False)


@pytest.mark.parametrize(
    ("subjects", "points"),
    [
        ((), ()),  # no brand
        (tuple(uuid.uuid4() for _ in range(6)), ()),  # a sixth line
        ((OLA, OLA), ()),
        ((OLA,), (point(1),)),  # a place nobody was sent in
        ((OLA, UBER), (point(0), point(0))),  # two points for one line at one time
    ],
)
def test_a_comparison_the_database_would_reject_is_refused(
    subjects: tuple[uuid.UUID, ...], points: tuple[TrendsPoint, ...]
) -> None:
    with pytest.raises(ValueError):
        Comparison(SCAN, subjects, points)


def test_a_full_comparison_is_accepted() -> None:
    subjects = (OLA, UBER, *(uuid.uuid4() for _ in range(3)))
    assert len(Comparison(SCAN, subjects, tuple(point(n) for n in range(5))).points) == 5

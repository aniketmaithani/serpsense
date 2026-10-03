"""Trends points and app ratings refuse what the database would reject (data-model §5)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from serpsense.domain.observation import AppRating, TrendsPoint

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query_index": 0, "observed_at": NOW, "interest": 101},
        {"query_index": 0, "observed_at": datetime(2026, 10, 3), "interest": 1},  # noqa: DTZ001
        {"query_index": 5, "observed_at": NOW, "interest": 1},  # a sixth line
        {"query_index": -1, "observed_at": NOW, "interest": 1},
        {"query_index": True, "observed_at": NOW, "interest": 1},
    ],
)
def test_invalid_trends_points_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        TrendsPoint(query="Ola", is_partial=False, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rating_hundredths": 99, "review_count": 1},
        {"rating_hundredths": 501, "review_count": 1},
        {"rating_hundredths": 400, "review_count": -1},
    ],
)
def test_invalid_ratings_are_refused(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        AppRating(**kwargs)


def test_boundaries_are_accepted() -> None:
    assert TrendsPoint("Ola", 0, NOW, 0, True).interest == 0
    assert TrendsPoint("inDrive", 4, NOW, 100, False).query_index == 4
    assert AppRating(100, 0).rating_hundredths == 100 and AppRating(500, 0).rating_hundredths == 500

"""A label is checked before it reaches the enrichment store (data-model §6)."""

import uuid

import pytest

from serpsense.domain.enums import Topic
from serpsense.ports.enrichment_store import MentionLabel

pytestmark = pytest.mark.unit


def label(**overrides: object) -> MentionLabel:
    values: dict[str, object] = {
        "mention_id": uuid.uuid4(),
        "revision": 1,
        "sentiment": -1,
        "severity": 30,
        "topic": Topic.PRICING,
        "is_complaint": True,
        "is_about_brand": True,
        "reason": "Fare went up at drop.",
    }
    return MentionLabel(**{**values, **overrides})  # type: ignore[arg-type]  # test builder


@pytest.mark.parametrize(
    "overrides",
    [{"sentiment": 2}, {"severity": 101}, {"revision": 0}, {"reason": " "}, {"reason": "x" * 501}],
)
def test_a_label_the_table_would_reject_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        label(**overrides)


def test_a_valid_label_is_accepted() -> None:
    assert label().topic is Topic.PRICING

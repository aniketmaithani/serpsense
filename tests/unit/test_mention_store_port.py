"""A sighting names the app or place its reviews cite (data-model §5)."""

import uuid

import pytest

from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import ParsedMention
from serpsense.ports.mention_store import Sighting

pytestmark = pytest.mark.unit

SCAN = uuid.uuid4()


@pytest.mark.parametrize("source", [MentionSource.PLAY_REVIEW, MentionSource.MAPS_REVIEW])
def test_reviews_without_what_they_cite_are_refused(source: MentionSource) -> None:
    seen = (ParsedMention(source, "review-1", "Late again", star_rating=1),)
    with pytest.raises(ValueError, match="cite"):
        Sighting(SCAN, seen)
    cited = {"brand_app_id": uuid.uuid4(), "brand_location_id": uuid.uuid4()}
    assert Sighting(SCAN, seen, **cited).mentions == seen

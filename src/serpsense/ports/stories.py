"""Port for the narratives a brand's pages show (BUILD_PLAN §13): the stories the model grouped
its negative mentions into, read-only and for the brand's owner only.

A narrative is model output (shown as AI-generated, with its prompt version). A mention belongs
to the narrative of its latest assignment, so a story's mentions and count are its current ones;
a story no mention belongs to any more is not shown.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import MentionSource
from serpsense.ports.overview import MentionRow


@dataclass(frozen=True, kw_only=True)
class StoryRow:
    narrative_id: uuid.UUID
    label: str
    summary: str
    prompt_version: str
    mentions: int
    sources: tuple[MentionSource, ...]
    last_grouped_at: datetime


@dataclass(frozen=True, kw_only=True)
class Story:
    brand_id: uuid.UUID
    brand_name: str
    row: StoryRow
    mentions: tuple[MentionRow, ...]  # the most negative first


class Stories(Protocol):
    def of_brand(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, *, limit: int
    ) -> list[StoryRow] | None:
        """The brand's stories, the largest first; None when the brand isn't the user's."""
        ...

    def story(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID
    ) -> Story | None:
        """One story with its mentions; None unless it is of this brand of the user's."""
        ...

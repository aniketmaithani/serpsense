"""Port for response drafts (ADR-0008, data-model §6): what the model drafts from, the drafts
it wrote, and the mentions each one cites.

Material and drafts are read for one user only: a narrative of a brand that isn't theirs, or is
archived, reads as missing. The material is the story and its current mentions (each mention's
latest assignment), labelled, worst first; mention text is public and untrusted.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import DraftKind, DraftPreset, MentionSource


@dataclass(frozen=True, kw_only=True)
class SourceMention:
    mention_id: uuid.UUID
    source: MentionSource
    text: str
    url: str | None
    sentiment: int | None  # the model's label; none until labelled


@dataclass(frozen=True, kw_only=True)
class DraftMaterial:
    owner_id: uuid.UUID  # the call is made, and billed, for the brand's owner
    brand_name: str
    story_label: str
    story_summary: str
    mentions: Sequence[SourceMention]


@dataclass(frozen=True, kw_only=True)
class NewDraft:
    narrative_id: uuid.UUID
    kind: DraftKind
    preset: DraftPreset
    text: str
    reasoning_summary: str | None
    cited: Sequence[uuid.UUID]  # mention ids, at least one
    prompt_version: str
    llm_call_id: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class DraftRow:
    draft_id: uuid.UUID
    kind: DraftKind
    preset: DraftPreset
    text: str
    reasoning_summary: str | None
    prompt_version: str
    created_at: datetime
    cited: Sequence[SourceMention]


class DraftStore(Protocol):
    def material(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID, *, limit: int
    ) -> DraftMaterial | None:
        """None unless the narrative is of this brand of the user's."""
        ...

    def record(self, draft: NewDraft) -> uuid.UUID:
        """Write the draft and its citations."""
        ...

    def thinking_drafts_since(self, user_id: uuid.UUID, since: datetime) -> int:
        """The user's draft calls at high-thinking or max effort since a time, failed ones
        included (they may have been billed)."""
        ...

    def drafts(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID, *, limit: int
    ) -> list[DraftRow]:
        """The narrative's newest drafts first; none unless it is of this brand of the user's."""
        ...

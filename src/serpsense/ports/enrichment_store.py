"""Port for the model's labels on mentions: what still needs labelling, and the labels (§6).

A mention's text to label is its latest revision (an edited review is labelled again). It is
pending while no label exists for that revision under the active prompt version. Only mentions
seen in a scan of the brand since a time are considered, so a prompt-version bump doesn't
re-process history and a failed run is picked up by the brand's next scan; and only the sources
the task labels (domain.labelling), so nothing another task owns can crowd the queue. A store
works inside the caller's unit of work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import MentionSource, Topic


@dataclass(frozen=True)
class PendingText:
    mention_id: uuid.UUID
    revision: int  # 1 is the mention's own text
    source: MentionSource
    language_code: str | None
    text: str


@dataclass(frozen=True)
class MentionLabel:
    mention_id: uuid.UUID
    revision: int
    sentiment: int  # -1, 0, 1
    severity: int  # 0-100
    topic: Topic
    is_complaint: bool
    is_about_brand: bool
    reason: str

    def __post_init__(self) -> None:
        if self.sentiment not in (-1, 0, 1) or not 0 <= self.severity <= 100:
            raise ValueError("sentiment is -1, 0 or 1 and severity 0-100")
        if self.revision < 1 or not self.reason.strip() or len(self.reason) > 500:
            raise ValueError("a revision from 1 and a non-blank reason of at most 500 characters")


class EnrichmentStore(Protocol):
    def pending(
        self,
        brand_id: uuid.UUID,
        *,
        prompt_version: str,
        sources: Collection[MentionSource],
        seen_since: datetime,
        limit: int,
    ) -> Sequence[PendingText]:
        """Texts of the brand's recently seen mentions of these sources with no label under this
        prompt, newest first."""
        ...

    def record(
        self,
        labels: Sequence[MentionLabel],
        *,
        prompt_version: str,
        llm_call_id: uuid.UUID,
        at: datetime,
    ) -> int:
        """Store the labels one call produced; returns how many were new."""
        ...

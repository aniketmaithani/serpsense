"""Port for narratives: the stories a brand's unfavourable mentions tell (data-model §6).

A mention waits for a story when its current label (`adapters/db/labels.py`) says it is about the
brand and unfavourable (negative, or a complaint), it was first seen since a time, and it is in
no narrative yet; once placed it stays in its story (assignments are append-only and the latest
per mention counts). A mention the model left out of every story keeps waiting until it ages
out, but is sent again only with a mention labelled after the brand's last grouping call (see
`considered_until`), so one-offs don't cost a call every scan. A narrative is open while a
mention joined it lately: the open ones are offered to the model, so it adds to a story before
starting another. A store works inside the caller's unit of work and never commits.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import LlmTask, MentionSource, Topic

MAX_LABEL, MAX_SUMMARY = 120, 2000


@dataclass(frozen=True, kw_only=True)
class UngroupedMention:
    mention_id: uuid.UUID
    source: MentionSource
    language_code: str | None
    text: str  # the latest revision's
    topic: Topic  # from its current label
    severity: int
    labelled_at: datetime  # when that label was made


@dataclass(frozen=True)
class OpenNarrative:
    narrative_id: uuid.UUID
    label: str
    summary: str
    mentions: int  # the mentions in it now


@dataclass(frozen=True)
class NewNarrative:
    narrative_id: uuid.UUID
    brand_id: uuid.UUID
    label: str
    summary: str

    def __post_init__(self) -> None:
        label, summary = self.label, self.summary
        if not label.strip() or len(label) > MAX_LABEL:
            raise ValueError(f"a narrative's label is non-blank, at most {MAX_LABEL} characters")
        if not summary.strip() or len(summary) > MAX_SUMMARY:
            raise ValueError(
                f"a narrative's summary is non-blank, at most {MAX_SUMMARY} characters"
            )


@dataclass(frozen=True)
class Assignment:
    mention_id: uuid.UUID
    narrative_id: uuid.UUID


class NarrativeStore(Protocol):
    def ungrouped(
        self,
        brand_id: uuid.UUID,
        *,
        first_seen_since: datetime,
        prompts: Mapping[LlmTask, str],
        limit: int,
    ) -> Sequence[UngroupedMention]:
        """The brand's unfavourable mentions first seen since then that are in no narrative,
        newest first; `prompts` are the labelling tasks' active prompts."""
        ...

    def considered_until(self, brand_id: uuid.UUID) -> datetime | None:
        """When the brand's latest grouping call in a scan was made, if it succeeded: a mention
        labelled before then was offered already. None when there is none or it failed."""
        ...

    def open(
        self, brand_id: uuid.UUID, *, active_since: datetime, limit: int
    ) -> Sequence[OpenNarrative]:
        """The brand's narratives a mention joined since then, the latest joined first."""
        ...

    def record(
        self,
        narratives: Sequence[NewNarrative],
        assignments: Sequence[Assignment],
        *,
        prompt_version: str,
        llm_call_id: uuid.UUID,
        at: datetime,
    ) -> int:
        """Store the placements one grouping call produced, and the new narratives they use; a
        mention placed already (a retried call) keeps its story, and a narrative nobody joins
        isn't stored. Returns how many placements were new."""
        ...

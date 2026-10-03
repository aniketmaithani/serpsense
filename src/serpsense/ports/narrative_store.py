"""Port for narratives: the stories a brand's unfavourable mentions tell (data-model §6).

A mention waits for a story when its current label (`adapters/db/labels.py`) says it is about the
brand and unfavourable (negative, or a complaint), it was first seen since a time, and it is in
no narrative yet; once placed it stays in its story (assignments are append-only and the latest
per mention counts). A mention the model left out of every story keeps waiting until it ages
out, but is sent again only with a mention labelled after the brand's last grouping call (see
`considered_until`), so one-offs don't cost a call every scan. A store works inside the
caller's unit of work and never commits.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import LlmTask, MentionSource, Topic


@dataclass(frozen=True, kw_only=True)
class UngroupedMention:
    mention_id: uuid.UUID
    source: MentionSource
    language_code: str | None
    text: str  # the latest revision's
    topic: Topic  # from its current label
    severity: int
    labelled_at: datetime  # when that label was made


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

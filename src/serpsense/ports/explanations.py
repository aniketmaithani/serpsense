"""Port for alert explanations (ADR-0008, data-model §6): what the model is told about an alert,
and the one explanation it writes for it.

A brief holds only facts the alert rests on: the brand, the rule, the scan's scores and crisis
components, and the mentions behind it (a story's current mentions for a spreading story; the
scan's unfavourable ones otherwise), labelled, worst first. Mention text is public and untrusted.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import AlertRule, CrisisComponent, CrisisLevel, MentionSource


@dataclass(frozen=True, kw_only=True)
class BriefMention:
    source: MentionSource
    text: str
    sentiment: int | None  # the model's label; none until labelled


@dataclass(frozen=True, kw_only=True)
class AlertBrief:
    alert_id: uuid.UUID
    scan_id: uuid.UUID
    owner_id: uuid.UUID  # the call is made, and billed, for the brand's owner
    brand_name: str
    competitor: bool  # the brand is tracked as another brand's competitor
    rule: AlertRule
    level: CrisisLevel | None
    previous_level: CrisisLevel | None  # the brand's scored scan before this one
    crisis: int
    health: int | None
    components: Mapping[CrisisComponent, int]  # a missing component counts as 0
    story_label: str | None  # a spreading story's
    story_summary: str | None
    mentions: Sequence[BriefMention]
    explained: bool  # an explanation exists already
    gone: bool = False  # the brand is archived or its owner's account deleted (ADR-0013)


class ExplanationStore(Protocol):
    def brief(self, alert_id: uuid.UUID) -> AlertBrief | None:
        """None for an alert that doesn't exist."""
        ...

    def record(
        self,
        alert_id: uuid.UUID,
        *,
        text: str,
        prompt_version: str,
        llm_call_id: uuid.UUID,
        at: datetime,
    ) -> bool:
        """Write the alert's explanation; False when it had one already."""
        ...

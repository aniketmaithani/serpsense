"""Port for what a scan saw: mentions, review revisions and observations (data-model §5).

Mentions are first-seen, so the text that was labelled never changes; a review whose text
changed becomes its next revision; a scan observes each mention once, at its best rank across
the scan's calls. Observations are append-only, so a collector records each source once per scan
with everything it saw. A store works inside the caller's unit of work and never commits.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import ParsedMention


@dataclass(frozen=True)
class Sighting:
    """Everything one collector saw in a scan, and the app or place its reviews cite."""

    scan_id: uuid.UUID
    mentions: tuple[ParsedMention, ...]
    brand_app_id: uuid.UUID | None = None  # cited by every Play review
    brand_location_id: uuid.UUID | None = None  # cited by every Maps review

    def __post_init__(self) -> None:
        sources = {mention.source for mention in self.mentions}
        if MentionSource.PLAY_REVIEW in sources and self.brand_app_id is None:
            raise ValueError("Play reviews cite the brand's app")
        if MentionSource.MAPS_REVIEW in sources and self.brand_location_id is None:
            raise ValueError("Maps reviews cite the brand's place")


@dataclass(frozen=True)
class Recorded:
    new: int  # mentions seen for the first time
    revised: int  # reviews whose text changed since it was last seen
    observed: int  # observations written; 0 when the sighting was already recorded


class MentionStore(Protocol):
    def record(self, sighting: Sighting, *, at: datetime) -> Recorded:
        """Store a scan's sighting; recording the same sighting again changes nothing."""
        ...

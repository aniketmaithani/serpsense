"""Port for what the pages show (BUILD_PLAN §13): read-only, and always for one owner.

Every read takes the signed-in user and sees only their brands (AGENTS §4: one scoped path); a
brand of someone else's reads exactly like one that doesn't exist. Labels are the model's
(shown as AI-generated); scores and levels are derived (v_scan_scores).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import (
    CrisisLevel,
    ScanStatus,
)


@dataclass(frozen=True, kw_only=True)
class BrandCard:
    brand_id: uuid.UUID
    name: str
    competitor_of: tuple[str, ...]  # the owner's brands that track this one
    health: int | None  # the latest scored scan's
    crisis: int | None
    level: CrisisLevel | None  # none while the brand warms up
    last_scan_at: datetime | None
    last_status: ScanStatus | None


class Overview(Protocol):
    def brands(self, user_id: uuid.UUID) -> list[BrandCard]:
        """The user's live brands: theirs first, then the ones they track as competitors."""
        ...

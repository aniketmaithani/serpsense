"""Port for what the pages show (BUILD_PLAN §13): read-only, and always for one owner.

Every read takes the signed-in user and sees only their brands (AGENTS §4: one scoped path); a
brand of someone else's reads exactly like one that doesn't exist. Labels are the model's
(shown as AI-generated); scores and levels are derived (v_scan_scores).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

from serpsense.domain.enums import (
    AlertRule,
    CrisisComponent,
    CrisisLevel,
    MentionSource,
    ScanStatus,
    Surface,
    SurfaceOutcome,
)
from serpsense.domain.scoring.tuning import DEFAULT_TUNING, CrisisTuning


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


@dataclass(frozen=True)
class TrendPoint:
    at: datetime
    health: int | None
    crisis: int
    level: CrisisLevel | None


@dataclass(frozen=True)
class SurfaceRow:
    surface: Surface
    outcome: SurfaceOutcome
    score: int | None  # none when it showed nothing about the brand


@dataclass(frozen=True, kw_only=True)
class MentionRow:
    source: MentionSource
    text: str
    url: str | None
    outlet: str | None
    position: int | None
    published_at: datetime | None
    sentiment: int | None  # the model's label; none until labelled
    reason: str | None


@dataclass(frozen=True)
class AlertRow:
    rule: AlertRule
    at: datetime
    title: str
    explanation: str | None = None  # the model's, once written (AI-generated)


@dataclass(frozen=True)
class BrandPage:
    card: BrandCard
    trend: tuple[TrendPoint, ...]  # oldest first
    surfaces: tuple[SurfaceRow, ...]  # the latest scored scan's, the one the card describes
    mentions: tuple[MentionRow, ...]  # that scan's, about the brand, worst first
    alerts: tuple[AlertRow, ...]  # newest first
    competitors: tuple[BrandCard, ...]
    # How the card's crisis was picked up: the latest scored scan's signals (0-100 each), the
    # brand's scored scans so far, and the tuning its level and alerts are read with.
    components: Mapping[CrisisComponent, int] = field(default_factory=lambda: MappingProxyType({}))
    scored_scans: int = 0
    tuning: CrisisTuning = DEFAULT_TUNING
    mention_page: int = 1  # of `mentions`, the page shown
    mention_pages: int = 1


class Overview(Protocol):
    def brands(self, user_id: uuid.UUID) -> list[BrandCard]:
        """The user's live brands: theirs first, then the ones they track as competitors."""
        ...

    def brand(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, *, mention_page: int = 1
    ) -> BrandPage | None:
        """One of the user's brands, with one page of its latest scan's mentions (a page past
        the last shows the last); None for a brand that isn't theirs or doesn't exist."""
        ...

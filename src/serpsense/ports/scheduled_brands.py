"""Port for what the dispatcher reads: brands on a schedule and what their scans need (§10).

A brand is on a schedule when it isn't archived, its owner isn't deleted and its latest schedule
version has an interval. Everything is read as of a time, so a version or scan dated later is
ignored. Settings documents are read whole and validated by the dispatcher, so one brand's bad
document can't stop the others.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Protocol

from serpsense.domain.estimator import BrandFacts


@dataclass(frozen=True)
class ScheduledBrand:
    brand_id: uuid.UUID
    interval_minutes: int
    timezone: str  # validated when the dispatcher builds the Schedule
    quiet_start: time | None
    quiet_end: time | None
    user_defaults: Mapping[str, Any]  # the owner's latest search defaults document, or {}
    brand_settings: Mapping[str, Any]  # the brand's latest search settings document, or {}
    languages: tuple[str, ...]  # brand_languages rows; empty leaves the settings' languages
    facts: BrandFacts
    # When the brand's latest scan that covers a slot was created: scheduled or "Scan now", not
    # a replay, and not one that failed or was skipped (it didn't give the slot its data).
    last_scan_at: datetime | None


class ScheduledBrands(Protocol):
    def scheduled_brands(self, as_of: datetime) -> Sequence[ScheduledBrand]: ...

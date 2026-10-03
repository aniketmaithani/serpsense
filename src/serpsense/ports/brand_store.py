"""Port for writing a user's brands and what they're monitored with (data-model §2, §3).

Writes are idempotent wherever the data model has a natural key: a brand by its owner and slug,
an alias by its brand and text (ignoring case), an app by its brand, store and id, a competitor
by the pair. Schedules and search settings are versions: a new one is written only when it
differs from the latest, so writing the same again changes nothing. A store works inside the
caller's unit of work and never commits.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from serpsense.domain.enums import AppStore
from serpsense.domain.schedule import Schedule

SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


@dataclass(frozen=True)
class NewBrand:
    owner_id: uuid.UUID
    name: str
    slug: str  # unique per owner; lowercase words joined by hyphens
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("a brand name is 1-120 characters, not all whitespace")
        if not SLUG.fullmatch(self.slug) or len(self.slug) > 64:
            raise ValueError("a slug is lowercase words joined by hyphens, at most 64 characters")


class BrandStore(Protocol):
    def brand(self, new: NewBrand) -> uuid.UUID:
        """The owner's brand with this slug, created if there is none."""
        ...

    def add_alias(self, brand_id: uuid.UUID, alias: str) -> bool:
        """False when the brand already has it."""
        ...

    def add_app(self, brand_id: uuid.UUID, store: AppStore, app_id: str) -> uuid.UUID:
        """The brand's app with this store id, added if it has none."""
        ...

    def link_competitor(self, brand_id: uuid.UUID, competitor_id: uuid.UUID) -> bool:
        """False when already linked; both brands must have one owner, and a brand at most
        four competitors (the database refuses otherwise)."""
        ...

    def set_schedule(self, brand_id: uuid.UUID, schedule: Schedule | None, *, at: datetime) -> bool:
        """A new schedule version, unless the latest is the same; True when one was written.
        None means scanned only on request (no interval)."""
        ...

    def stop_schedule(self, brand_id: uuid.UUID, *, at: datetime) -> bool:
        """A schedule version with no interval (manual scans only) keeping the timezone; False
        when the brand has no schedule or already has no interval."""
        ...

    def set_languages(self, brand_id: uuid.UUID, languages: Sequence[str]) -> bool:
        """The brand's own languages (lowercase BCP-47 tags, the caller validated) become exactly
        these; True when any were added or removed."""
        ...

    def set_search_settings(
        self, brand_id: uuid.UUID, document: Mapping[str, Any], *, at: datetime
    ) -> bool:
        """A new search settings version (a document the caller validated), unless the latest
        is the same; True when one was written."""
        ...

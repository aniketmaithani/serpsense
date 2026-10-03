"""Port for what a scan's worker needs to know about the brand behind it (BUILD_PLAN §10).

Read in the unit of work that claims the scan, and again before the model is called and alerts
are written, since the brand can be archived or its owner's account deleted while the scan runs.
Values are as stored: the scan service turns them into a collector target and refuses the scan
if they can't make one. A worker-only read by the scan id the system issued, so it isn't scoped
to a user: user-facing reads go through the scoped query path (AGENTS §4). Every linked
competitor is compared, archived or not; unlinking one takes it out of the comparison.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class NamedBrand:
    brand_id: uuid.UUID
    name: str


@dataclass(frozen=True)
class StoreApp:
    brand_app_id: uuid.UUID
    package: str  # the Google Play id


@dataclass(frozen=True)
class ScanTarget:
    scan_id: uuid.UUID
    brand: NamedBrand
    owner_id: uuid.UUID  # every search and model call of the scan is billed to the owner
    settings_snapshot: Mapping[str, Any]  # as resolved when the scan was created
    aliases: tuple[str, ...]
    competitors: tuple[NamedBrand, ...]  # at most four, in a stable order
    apps: tuple[StoreApp, ...]  # Google Play apps, in a stable order
    brand_archived: bool
    owner_deleted: bool


class ScanTargets(Protocol):
    def for_scan(self, scan_id: uuid.UUID) -> ScanTarget | None:
        """The scan's brand as it is now; None when there is no such scan."""
        ...

"""Port for scans and their status history (data-model §4, AGENTS §5, ADR-0005).

Postgres is the source of truth: a scan is created with `INSERT … ON CONFLICT DO NOTHING`, so
both the slot rule and the one-active-scan rule apply, and every status change is a
compare-and-set that writes its transition row. A store works inside the caller's unit of work
and never commits, so a finish commits or rolls back with the alerts and emails it caused.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from serpsense.domain.enums import ScanTrigger, Surface, SurfaceOutcome
from serpsense.domain.scan_state import Transition


@dataclass(frozen=True)
class NewScan:
    brand_id: uuid.UUID
    trigger: ScanTrigger
    settings_snapshot: Mapping[str, Any]  # resolved settings, immutable once the scan exists
    estimated_searches: int
    created_at: datetime
    scheduled_for: datetime | None = None  # the slot, for scheduled scans only
    requested_by: uuid.UUID | None = None  # the user, for "Scan now" only

    def __post_init__(self) -> None:
        if (self.trigger is ScanTrigger.SCHEDULE) != (self.scheduled_for is not None):
            raise ValueError("a slot exactly for scheduled scans")
        if (self.trigger is ScanTrigger.MANUAL) != (self.requested_by is not None):
            raise ValueError("a requester exactly for manual scans")
        if self.estimated_searches < 0:
            raise ValueError("estimated searches can't be negative")


# The format the table checks (`ck_scan_surface_results_error_code_format`).
ERROR_CODE = re.compile(r"[a-z][a-z0-9_.]{0,63}")


@dataclass(frozen=True)
class SurfaceResult:
    """How collecting one surface went in a scan."""

    surface: Surface
    outcome: SurfaceOutcome
    error_code: str | None = None  # exactly for failed surfaces, e.g. `serpapi.http_5xx`

    def __post_init__(self) -> None:
        if (self.error_code is None) == (self.outcome is SurfaceOutcome.FAILED):
            raise ValueError("an error code exactly for a failed surface")
        if self.error_code is not None and not ERROR_CODE.fullmatch(self.error_code):
            raise ValueError("an error code is a lowercase dotted name")


class ScanStore(Protocol):
    def create(self, scan: NewScan) -> uuid.UUID | None:
        """A queued scan and its creation transition; None if the slot is taken or the brand
        already has an active scan."""
        ...

    def move(self, scan_id: uuid.UUID, transition: Transition, *, at: datetime) -> bool:
        """Compare-and-set from `transition.from_status`; False (and nothing written) when the
        scan has moved on, e.g. a redelivered claim. A creation transition raises
        IllegalTransition: scans come into existence only through `create`."""
        ...

    def record_surfaces(self, scan_id: uuid.UUID, results: Sequence[SurfaceResult]) -> int:
        """Each surface's outcome, once per scan: a retried scan keeps what was recorded first.
        Returns how many were written."""
        ...

    def queued_before(self, at: datetime) -> Sequence[uuid.UUID]:
        """Scans still queued that were created before `at` (lost enqueues)."""
        ...

    def running_before(self, at: datetime) -> Sequence[uuid.UUID]:
        """Scans still running that were claimed before `at` (stuck)."""
        ...

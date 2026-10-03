"""Scan now (BUILD_PLAN §13): the owner asks for a scan of one of their brands, off schedule.

The scan is built like a scheduled one (the owner's defaults, the brand's settings and languages,
capped by the admin's per-scan limit) with the `manual` trigger, the asker as its requester and
no slot, and its job is sent after the commit. A brand has at most one scan queued or running
(`uq_scans_brand_id_active`), so asking during one is refused, and so is asking within 15
minutes of the brand's last scan, so manual scans can't drain the day's global cap. A scan the
owner's searches left this month can't cover is refused up front (the scan service checks
again when it runs). A brand that isn't the asker's, or is archived, reads as missing.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import ValidationError

from serpsense.domain import usage
from serpsense.domain.enums import ScanTrigger
from serpsense.domain.estimator import estimate
from serpsense.domain.settings.search import for_brand
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.scan_store import NewScan
from serpsense.ports.search_ledger import SearchUsage
from serpsense.ports.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)


COOLDOWN = timedelta(minutes=15)


class Requested(StrEnum):
    QUEUED = "queued"
    BUSY = "busy"  # a scan of the brand is already queued or running
    TOO_SOON = "too_soon"  # the brand's last scan was created under COOLDOWN ago
    OVER_BUDGET = "over_budget"  # the owner's searches left this month can't cover it
    MISSING = "missing"  # not the asker's brand, archived, or no brand at all
    INVALID = "invalid"  # settings a scan can't use


@dataclass(frozen=True)
class ScanNowLimits:
    max_searches_per_scan: int  # the admin limit (MAX_SEARCHES_PER_SCAN)
    monthly_searches: int  # a user's monthly budget when none was set


class ScanNow:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        searches: SearchUsage,
        clock: Clock,
        limits: ScanNowLimits,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._searches = searches
        self._clock = clock
        self._limits = limits

    def request(self, user_id: uuid.UUID, brand_id: uuid.UUID) -> Requested:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            inputs = uow.schedules.scan_inputs(user_id, brand_id, as_of=now)
            if inputs is None:
                return Requested.MISSING
            if inputs.last_scan_at is not None and now - inputs.last_scan_at < COOLDOWN:
                return Requested.TOO_SOON
            try:
                settings = for_brand(
                    inputs.user_defaults, inputs.brand_settings, inputs.languages
                ).capped(self._limits.max_searches_per_scan)
            except ValidationError as exc:
                error = type(exc).__name__
                log.warning("scan_now.rejected", brand_id=str(brand_id), error=error)
                return Requested.INVALID
            searches = estimate(settings, inputs.facts)
            if not self._affordable(user_id, min(searches, settings.max_searches), now):
                return Requested.OVER_BUDGET
            scan = NewScan(
                brand_id=brand_id,
                trigger=ScanTrigger.MANUAL,
                settings_snapshot=settings.model_dump(mode="json"),
                estimated_searches=searches,
                created_at=now,
                requested_by=user_id,
            )
            scan_id = uow.scans.create(scan)
            if scan_id is None:
                return Requested.BUSY
            uow.jobs.run_scan(scan_id)
        log.info("scan.queued", scan_id=str(scan_id), brand_id=str(brand_id), trigger="manual")
        return Requested.QUEUED

    def _affordable(self, user_id: uuid.UUID, searches: int, at: datetime) -> bool:
        budget = self._searches.monthly_budget(user_id, at=at)
        used = self._searches.live_calls(since=usage.month_start(at), user_id=user_id)
        default = self._limits.monthly_searches
        return searches <= usage.searches_left(budget, default=default, used=used)

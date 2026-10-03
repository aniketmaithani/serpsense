"""Dispatch scheduled scans (BUILD_PLAN §10 step 1, ADR-0005).

Every few minutes each brand on a schedule gets a queued scan for its current slot, unless the
slot falls in its quiet hours or a scan already covers it: a scheduled scan or a "Scan now"
created in the slot that didn't fail or get skipped, so the searches aren't spent twice. A slot
whose quiet hours end part-way runs late, on the first dispatch after they end. The snapshot
carries the admin's per-scan limit. Each brand is its own transaction, so one brand's bad
schedule or settings can't hold up the rest, and its job is sent after the commit.
"""

from collections import Counter
from datetime import datetime
from enum import StrEnum

from pydantic import ValidationError

from serpsense.domain.enums import ScanTrigger
from serpsense.domain.estimator import estimate
from serpsense.domain.schedule import InvalidSchedule, Schedule
from serpsense.domain.settings.search import for_brand
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.scan_store import NewScan
from serpsense.ports.scheduled_brands import ScheduledBrand
from serpsense.ports.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)


class Outcome(StrEnum):
    CREATED = "created"
    QUIET = "quiet"  # inside the brand's quiet hours
    COVERED = "covered"  # a scan was already created in this slot
    ACTIVE = "active"  # the slot is taken or the brand has a scan queued or running
    INVALID = "invalid"  # a schedule or settings document the dispatcher can't use


class Dispatcher:
    def __init__(
        self, unit_of_work: UnitOfWorkFactory, clock: Clock, *, max_searches_per_scan: int
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._max_searches = max_searches_per_scan  # the admin limit (MAX_SEARCHES_PER_SCAN)

    def dispatch(self) -> Counter[Outcome]:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            brands = uow.schedules.scheduled_brands(as_of=now)
        outcomes = Counter(self._dispatch(brand, now) for brand in brands)
        log.info("dispatch.completed", **{outcome.value: outcomes[outcome] for outcome in Outcome})
        return outcomes

    def _dispatch(self, brand: ScheduledBrand, now: datetime) -> Outcome:
        try:
            schedule = Schedule(
                brand.interval_minutes, brand.timezone, brand.quiet_start, brand.quiet_end
            )
            settings = for_brand(brand.user_defaults, brand.brand_settings, brand.languages)
        except (InvalidSchedule, ValidationError) as exc:
            error = type(exc).__name__
            log.warning("dispatch.brand_rejected", brand_id=str(brand.brand_id), error=error)
            return Outcome.INVALID
        settings = settings.capped(self._max_searches)
        if schedule.is_quiet(now):
            return Outcome.QUIET
        slot = schedule.slot(now)
        if brand.last_scan_at is not None and brand.last_scan_at >= slot:
            return Outcome.COVERED
        scan = NewScan(
            brand_id=brand.brand_id,
            trigger=ScanTrigger.SCHEDULE,
            settings_snapshot=settings.model_dump(mode="json"),
            estimated_searches=estimate(settings, brand.facts),
            created_at=now,
            scheduled_for=slot,
        )
        with self._unit_of_work() as uow:
            scan_id = uow.scans.create(scan)
            if scan_id is None:
                return Outcome.ACTIVE
            uow.jobs.run_scan(scan_id)
        log.info("scan.queued", scan_id=str(scan_id), brand_id=str(brand.brand_id))
        return Outcome.CREATED

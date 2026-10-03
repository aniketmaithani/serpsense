"""Play recorded scans into a database (BUILD_PLAN §21, §23, `SERPSENSE_MODE=replay`).

Every recorded scan of every brand is played in the order the scans ran, each at its recorded
time: the clock is set to it, a `replay` scan is created then (no slot, no requester, its
recorded settings, data-model §3), and the scan service runs it on the spot, through the whole
pipeline: collect, label, score, alert, and emails into the outbox, which its worker sends. So a
fresh install shows a trend, stories and alerts instead of one scan. A recorded scan already
played (a replay scan of the brand created at its time) is not played again, so loading twice,
or loading a longer recording later, plays only what is new. A brand with a scan queued or
running is left for the next load.
"""

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from serpsense.domain.enums import ScanStatus, ScanTrigger
from serpsense.domain.estimator import estimate
from serpsense.domain.settings.search import SearchSettings
from serpsense.observability import get_logger
from serpsense.ports.clock import SettableClock
from serpsense.ports.scan_store import NewScan
from serpsense.ports.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)


@dataclass(frozen=True)
class RecordedScan:
    brand: str  # the brand's slug
    recorded_at: datetime
    settings: Mapping[str, Any]  # the scan's settings snapshot, so it asks what it asked then


class Played(StrEnum):
    PLAYED = "played"
    ALREADY = "already"  # played by an earlier load
    BUSY = "busy"  # the brand had a scan queued or running
    MISSING = "missing"  # no such brand of the owner's


class Scans(Protocol):
    def run(self, scan_id: uuid.UUID) -> ScanStatus | None: ...


class ReplayLoader:
    def __init__(self, unit_of_work: UnitOfWorkFactory, scans: Scans, clock: SettableClock) -> None:
        self._unit_of_work, self._scans, self._clock = unit_of_work, scans, clock

    def load(
        self, owner_id: uuid.UUID, brands: Mapping[str, uuid.UUID], plan: Sequence[RecordedScan]
    ) -> Counter[Played]:
        """Play the recorded scans of the owner's brands (by slug), oldest first."""
        played: Counter[Played] = Counter()
        for scan in sorted(plan, key=lambda s: (s.recorded_at, s.brand)):
            brand_id = brands.get(scan.brand)
            outcome = Played.MISSING if brand_id is None else self._play(owner_id, brand_id, scan)
            played[outcome] += 1
        log.info("replay.loaded", **{outcome.value: played[outcome] for outcome in Played})
        return played

    def _play(self, owner_id: uuid.UUID, brand_id: uuid.UUID, recorded: RecordedScan) -> Played:
        self._clock.set(recorded.recorded_at)
        settings = SearchSettings.model_validate(dict(recorded.settings))
        with self._unit_of_work() as uow:
            if recorded.recorded_at in uow.scans.replayed_at(brand_id):
                return Played.ALREADY
            inputs = uow.schedules.scan_inputs(owner_id, brand_id, as_of=self._clock.now())
            if inputs is None:
                return Played.MISSING
            scan_id = uow.scans.create(
                NewScan(
                    brand_id=brand_id,
                    trigger=ScanTrigger.REPLAY,
                    settings_snapshot=settings.model_dump(mode="json"),
                    estimated_searches=estimate(settings, inputs.facts),
                    created_at=recorded.recorded_at,
                )
            )
        if scan_id is None:
            return Played.BUSY
        status = self._scans.run(scan_id)
        log.info("replay.scan_played", scan_id=str(scan_id), brand_id=str(brand_id), status=status)
        return Played.PLAYED

"""Recover lost and stuck scans (ADR-0005, BUILD_PLAN §10).

A queued scan whose nudge never reached a worker is sent again; the claim's compare-and-set makes
a duplicate harmless, and a worker that claims a scan of an archived brand finishes it skipped.
A running scan claimed longer ago than the time limit allows is failed as timed out, with the
same compare-and-set as any finish, so a worker finishing at that moment wins or loses cleanly.
The re-sends and each timeout are separate units of work, so one failure can't undo the rest.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from serpsense.domain.enums import ScanStatus
from serpsense.domain.scan_state import Transition, TransitionReason
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)

LOST_AFTER = timedelta(minutes=2)
TIMED_OUT = Transition(ScanStatus.RUNNING, ScanStatus.FAILED, TransitionReason.TIMED_OUT)


@dataclass(frozen=True)
class Swept:
    resent: int
    timed_out: int


class Sweeper:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        clock: Clock,
        *,
        stuck_after: timedelta,  # the scan task's time limit plus a margin
        lost_after: timedelta = LOST_AFTER,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._stuck_after = stuck_after
        self._lost_after = lost_after

    def sweep(self) -> Swept:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            lost = uow.scans.queued_before(now - self._lost_after)
            for scan_id in lost:
                uow.jobs.run_scan(scan_id)
            stuck = uow.scans.running_before(now - self._stuck_after)
        timed_out = sum(self._time_out(scan_id, now) for scan_id in stuck)
        log.info("sweep.completed", resent=len(lost), timed_out=timed_out)
        return Swept(resent=len(lost), timed_out=timed_out)

    def _time_out(self, scan_id: uuid.UUID, now: datetime) -> bool:
        with self._unit_of_work() as uow:
            if not uow.scans.move(scan_id, TIMED_OUT, at=now):
                return False  # it finished meanwhile
        log.warning("scan.timed_out", scan_id=str(scan_id))
        return True

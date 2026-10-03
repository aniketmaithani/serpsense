"""The recovery sweep: lost scans are sent again, stuck ones fail as timed out (ADR-0005)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from serpsense.domain.enums import ScanStatus, ScanTrigger
from serpsense.domain.scan_state import Transition, TransitionReason
from serpsense.ports.scan_store import NewScan
from serpsense.services.sweep import Sweeper, Swept
from tests.fakes import FakeUnitOfWork, FixedClock, InMemoryScans, StaticSchedules

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
CLAIM = Transition(ScanStatus.QUEUED, ScanStatus.RUNNING, TransitionReason.CLAIMED)


def scan(scans: InMemoryScans, created: datetime) -> uuid.UUID:
    new = NewScan(uuid.uuid4(), ScanTrigger.SCHEDULE, {}, 6, created, scheduled_for=created)
    scan_id = scans.create(new)
    assert scan_id is not None
    return scan_id


def sweep(scans: InMemoryScans) -> tuple[Swept, FakeUnitOfWork]:
    uow = FakeUnitOfWork(scans, StaticSchedules([]))
    swept = Sweeper(lambda: uow, FixedClock(NOW), stuck_after=20 * MINUTE).sweep()
    return swept, uow


def test_a_scan_queued_for_more_than_two_minutes_is_sent_again() -> None:
    scans = InMemoryScans()
    lost, fresh = scan(scans, NOW - 3 * MINUTE), scan(scans, NOW - MINUTE)
    swept, uow = sweep(scans)
    assert swept == Swept(resent=1, timed_out=0)
    assert uow.sent.scans == [lost] and fresh not in uow.sent.scans
    assert scans.status[lost] is ScanStatus.QUEUED  # the worker's claim decides


def test_a_scan_running_past_the_limit_fails_as_timed_out() -> None:
    scans = InMemoryScans()
    stuck, busy = scan(scans, NOW - MINUTE), scan(scans, NOW - MINUTE)
    scans.move(stuck, CLAIM, at=NOW - 21 * MINUTE)
    scans.move(busy, CLAIM, at=NOW - 19 * MINUTE)
    with capture_logs() as logs:
        swept, _ = sweep(scans)
    assert swept == Swept(resent=0, timed_out=1)
    assert (scans.status[stuck], scans.status[busy]) == (ScanStatus.FAILED, ScanStatus.RUNNING)
    (_, transition, at) = scans.transitions[-1]
    assert (transition.reason, at) == (TransitionReason.TIMED_OUT, NOW)
    assert {"event": "scan.timed_out", "scan_id": str(stuck), "log_level": "warning"} in logs


class FinishesWhileSwept(InMemoryScans):
    """The worker finishes the scan between the sweep's read and its move."""

    def running_before(self, at: datetime) -> list[uuid.UUID]:
        stuck = list(super().running_before(at))
        finished = Transition(ScanStatus.RUNNING, ScanStatus.SUCCEEDED, TransitionReason.COMPLETED)
        for scan_id in stuck:
            self.move(scan_id, finished, at=NOW)
        return stuck


def test_a_scan_that_finished_meanwhile_is_left_alone() -> None:
    scans = FinishesWhileSwept()
    done = scan(scans, NOW - MINUTE)
    scans.move(done, CLAIM, at=NOW - 30 * MINUTE)
    with capture_logs() as logs:
        swept, uow = sweep(scans)
    assert swept == Swept(resent=0, timed_out=0) and uow.sent.scans == []
    assert scans.status[done] is ScanStatus.SUCCEEDED
    assert "scan.timed_out" not in [entry["event"] for entry in logs]

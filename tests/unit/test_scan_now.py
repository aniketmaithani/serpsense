"""Scan now: one queued manual scan for the owner's brand, sent after commit (BUILD_PLAN §13)."""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from serpsense.domain.enums import ScanStatus, ScanTrigger
from serpsense.domain.estimator import BrandFacts
from serpsense.ports.scheduled_brands import ScanInputs
from serpsense.ports.unit_of_work import UnitOfWork
from serpsense.services.scan_now import Requested, ScanNow, ScanNowLimits
from tests.fakes import FakeUnitOfWork, FixedClock, InMemoryScans, StaticSchedules

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
OWNER, OLA = uuid.uuid4(), uuid.uuid4()
INPUTS = ScanInputs(
    user_defaults={"max_searches": 30},
    brand_settings={"maps": {"enabled": False}},
    languages=("hi", "en"),
    facts=BrandFacts(apps=1, locations=0),
)


@dataclass
class Usage:
    """The owner's searches this month."""

    used: int = 0
    budget: int | None = None

    def monthly_budget(self, user_id: uuid.UUID, *, at: datetime) -> int | None:
        return self.budget

    def live_calls(
        self, *, since: datetime, user_id: uuid.UUID | None = None, scan_id: uuid.UUID | None = None
    ) -> int:
        assert (since, user_id) == (datetime(2026, 10, 1, tzinfo=UTC), OWNER)
        return self.used


def scan_now(
    inputs: ScanInputs = INPUTS, searches: Usage | None = None
) -> tuple[ScanNow, FakeUnitOfWork]:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules(owned={(OWNER, OLA): inputs}))
    factory: UnitOfWork = uow
    limits = ScanNowLimits(max_searches_per_scan=12, monthly_searches=240)
    return ScanNow(lambda: factory, searches or Usage(), FixedClock(NOW), limits), uow


def test_the_owner_gets_a_queued_manual_scan_sent_after_commit() -> None:
    service, uow = scan_now()
    assert service.request(OWNER, OLA) is Requested.QUEUED
    ((scan_id, scan),) = uow.scans.scans.items()
    assert (scan.trigger, scan.requested_by, scan.scheduled_for) == (
        ScanTrigger.MANUAL,
        OWNER,
        None,
    )
    assert (scan.brand_id, scan.created_at, uow.schedules.read_as_of) == (OLA, NOW, NOW)
    snapshot = scan.settings_snapshot
    assert snapshot["max_searches"] == 12  # the admin's limit wins over the owner's 30
    assert snapshot["languages"] == ["hi", "en"] and snapshot["maps"]["enabled"] is False
    # search page 2, autocomplete 3 x 2 languages, news 2, Trends 2, Play 2: 14, capped at 12.
    assert scan.estimated_searches == 14 and uow.sent.scans == [scan_id]


def test_asking_while_a_scan_is_queued_or_running_is_refused() -> None:
    service, uow = scan_now()
    service.request(OWNER, OLA)
    (first,) = uow.scans.scans
    uow.scans.status[first] = ScanStatus.RUNNING
    assert service.request(OWNER, OLA) is Requested.BUSY
    assert len(uow.scans.scans) == 1 and len(uow.sent.scans) == 1


def test_a_brand_that_isnt_the_askers_reads_as_missing() -> None:
    service, uow = scan_now()
    assert service.request(uuid.uuid4(), OLA) is Requested.MISSING
    assert service.request(OWNER, uuid.uuid4()) is Requested.MISSING
    assert uow.scans.scans == {} and uow.sent.scans == []


def test_settings_a_scan_cant_use_are_refused() -> None:
    broken = ScanInputs({"max_searches": "lots"}, {}, (), BrandFacts(apps=0, locations=0))
    service, uow = scan_now(broken)
    assert service.request(OWNER, OLA) is Requested.INVALID
    assert uow.scans.scans == {}


def test_asking_again_within_the_cooldown_is_refused() -> None:
    recently = replace(INPUTS, last_scan_at=NOW - timedelta(minutes=14))
    service, uow = scan_now(recently)
    assert service.request(OWNER, OLA) is Requested.TOO_SOON and uow.scans.scans == {}
    earlier = replace(INPUTS, last_scan_at=NOW - timedelta(minutes=15))
    assert scan_now(earlier)[0].request(OWNER, OLA) is Requested.QUEUED


def test_a_scan_the_months_searches_cant_cover_is_refused() -> None:
    service, uow = scan_now(searches=Usage(used=229))  # 11 left of the default 240; it needs 12
    assert service.request(OWNER, OLA) is Requested.OVER_BUDGET and uow.scans.scans == {}
    assert scan_now(searches=Usage(used=228))[0].request(OWNER, OLA) is Requested.QUEUED
    assert scan_now(searches=Usage(budget=500, used=480))[0].request(OWNER, OLA) is Requested.QUEUED

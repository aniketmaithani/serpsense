"""The dispatcher: one queued scan per brand per slot, sent after commit (BUILD_PLAN §10)."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest
from structlog.testing import capture_logs

from serpsense.domain.enums import ScanTrigger
from serpsense.domain.estimator import BrandFacts
from serpsense.ports.scheduled_brands import ScheduledBrand
from serpsense.ports.unit_of_work import UnitOfWork
from serpsense.services.dispatch import Dispatcher, Outcome
from tests.fakes import FakeUnitOfWork, FixedClock, InMemoryScans, StaticSchedules

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 6, 47, tzinfo=UTC)  # 12:17 IST
SLOT = datetime(2026, 10, 3, 6, 30, tzinfo=UTC)  # 12:00 IST, for a 6-hour schedule
OLA = ScheduledBrand(
    brand_id=uuid.uuid4(),
    interval_minutes=360,
    timezone="Asia/Kolkata",
    quiet_start=None,
    quiet_end=None,
    user_defaults={"autocomplete": {"prefixes": ["{brand} "]}},
    brand_settings={"maps": {"enabled": False}},
    languages=("en",),
    facts=BrandFacts(apps=1, locations=0),
    last_scan_at=None,
)


def dispatch(*brands: ScheduledBrand) -> tuple[FakeUnitOfWork, dict[Outcome, int]]:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules(brands))
    factory: UnitOfWork = uow
    outcomes = Dispatcher(lambda: factory, FixedClock(NOW), max_searches_per_scan=40).dispatch()
    return uow, dict(outcomes)


def test_a_due_brand_gets_a_queued_scan_for_its_slot() -> None:
    uow, outcomes = dispatch(OLA)
    assert outcomes == {Outcome.CREATED: 1}
    ((scan_id, scan),) = uow.scans.scans.items()
    assert (scan.brand_id, scan.trigger, scan.scheduled_for) == (
        OLA.brand_id,
        ScanTrigger.SCHEDULE,
        SLOT,
    )
    assert scan.created_at == NOW
    snapshot = scan.settings_snapshot
    assert (snapshot["languages"], snapshot["autocomplete"]["prefixes"]) == (["en"], ["{brand} "])
    assert snapshot["maps"]["enabled"] is False  # the brand's document over the defaults
    # search page 1 + AI Overview 1, autocomplete 1, news 1, Trends 2, Play 2.
    assert scan.estimated_searches == 8
    assert uow.sent.scans == [scan_id]


def test_dispatching_again_in_the_same_slot_creates_nothing() -> None:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([OLA]))
    dispatcher = Dispatcher(lambda: uow, FixedClock(NOW), max_searches_per_scan=40)
    dispatcher.dispatch()
    assert dispatcher.dispatch() == {Outcome.ACTIVE: 1}  # still queued: the slot is taken
    assert len(uow.sent.scans) == 1


@pytest.mark.parametrize(
    ("brand", "outcome"),
    [
        (replace(OLA, quiet_start=time(12), quiet_end=time(13)), Outcome.QUIET),
        (replace(OLA, last_scan_at=SLOT + timedelta(minutes=5)), Outcome.COVERED),  # Scan now
        (replace(OLA, timezone="America"), Outcome.INVALID),
        (replace(OLA, brand_settings={"search_page": {"pages": 9}}), Outcome.INVALID),
    ],
)
def test_a_brand_that_isnt_due_gets_no_scan(brand: ScheduledBrand, outcome: Outcome) -> None:
    uow, outcomes = dispatch(brand)
    assert outcomes == {outcome: 1}
    assert uow.scans.scans == {} and uow.sent.scans == []


def test_a_scan_before_the_slot_doesnt_cover_it() -> None:
    _, outcomes = dispatch(replace(OLA, last_scan_at=SLOT - timedelta(seconds=1)))
    assert outcomes == {Outcome.CREATED: 1}


def test_one_bad_brand_doesnt_stop_the_others() -> None:
    broken = replace(OLA, brand_id=uuid.uuid4(), timezone="Mars/Olympus")
    uber = replace(OLA, brand_id=uuid.uuid4())
    with capture_logs() as logs:
        uow, outcomes = dispatch(broken, uber)
    assert outcomes == {Outcome.INVALID: 1, Outcome.CREATED: 1}
    assert [s.brand_id for s in uow.scans.scans.values()] == [uber.brand_id]
    events = {entry["event"]: entry for entry in logs}
    assert events["dispatch.brand_rejected"]["brand_id"] == str(broken.brand_id)
    assert events["dispatch.brand_rejected"]["error"] == "InvalidSchedule"
    assert (events["dispatch.completed"]["created"], events["dispatch.completed"]["invalid"]) == (
        1,
        1,
    )


def test_the_reading_is_as_of_now_and_the_admin_limit_caps_the_snapshot() -> None:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([OLA]))
    Dispatcher(lambda: uow, FixedClock(NOW), max_searches_per_scan=10).dispatch()
    assert uow.schedules.read_as_of == NOW
    ((_, scan),) = uow.scans.scans.items()
    assert scan.settings_snapshot["max_searches"] == 10  # the brand's 25, under the admin's 10


def test_a_slot_whose_quiet_hours_end_part_way_runs_late() -> None:
    """22:00-07:00 IST quiet hours, 6-hour slots: at 07:05 IST the 06:00 slot still runs."""
    brand = replace(OLA, quiet_start=time(22), quiet_end=time(7))
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([brand]))
    at = datetime(2026, 10, 3, 1, 35, tzinfo=UTC)  # 07:05 IST
    Dispatcher(lambda: uow, FixedClock(at), max_searches_per_scan=40).dispatch()
    ((_, scan),) = uow.scans.scans.items()
    assert scan.scheduled_for == datetime(2026, 10, 3, 0, 30, tzinfo=UTC)  # 06:00 IST

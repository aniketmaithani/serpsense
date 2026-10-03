"""Playing recorded scans: in the order they ran, each at its time, once (services/replay.py)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from serpsense.adapters.replay.clock import ReplayClock
from serpsense.domain.enums import ScanStatus, ScanTrigger
from serpsense.domain.estimator import BrandFacts
from serpsense.ports.scheduled_brands import ScanInputs
from serpsense.ports.unit_of_work import UnitOfWork
from serpsense.services.replay import Played, RecordedScan, ReplayLoader
from tests.fakes import FakeUnitOfWork, InMemoryScans, StaticSchedules

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 3, 5, 25, tzinfo=UTC)
HALF_DAY = timedelta(hours=12)
OWNER, OLA, UBER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
SETTINGS = {"languages": ["en"], "max_searches": 8}
INPUTS = ScanInputs({}, {}, (), BrandFacts(apps=1, locations=0))


class Scans:
    """Runs each scan as the scan service would: it ends, and the time it ran is noted."""

    def __init__(self, uow: FakeUnitOfWork, clock: ReplayClock) -> None:
        self.uow, self.clock, self.ran = uow, clock, list[tuple[uuid.UUID, datetime]]()

    def run(self, scan_id: uuid.UUID) -> ScanStatus | None:
        self.ran.append((scan_id, self.clock.now()))
        self.uow.scans.status[scan_id] = ScanStatus.SUCCEEDED
        return ScanStatus.SUCCEEDED


def loader() -> tuple[ReplayLoader, FakeUnitOfWork, Scans]:
    owned = {(OWNER, OLA): INPUTS, (OWNER, UBER): INPUTS}
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules(owned=owned))
    factory: UnitOfWork = uow
    clock = ReplayClock()
    scans = Scans(uow, clock)
    return ReplayLoader(lambda: factory, scans, clock), uow, scans


PLAN = [
    RecordedScan("ola", T0 + HALF_DAY, SETTINGS),
    RecordedScan("uber", T0, SETTINGS),
    RecordedScan("ola", T0, {**SETTINGS, "max_searches": 7}),
]
BRANDS = {"ola": OLA, "uber": UBER}


def test_recorded_scans_are_played_in_the_order_they_ran_each_at_its_time() -> None:
    load, uow, scans = loader()
    assert load.load(OWNER, BRANDS, PLAN) == {Played.PLAYED: 3}
    played = [uow.scans.scans[scan_id] for scan_id, _ in scans.ran]
    assert [(s.brand_id, s.created_at) for s in played] == [
        (OLA, T0),  # brands that ran at the same moment go by slug
        (UBER, T0),
        (OLA, T0 + HALF_DAY),
    ]
    assert all(s.trigger is ScanTrigger.REPLAY and s.requested_by is None for s in played)
    assert all(s.scheduled_for is None for s in played)
    assert played[0].settings_snapshot["max_searches"] == 7  # as it was recorded
    for (_, ran_at), scan in zip(scans.ran, played, strict=True):
        assert scan.created_at < ran_at < scan.created_at + timedelta(seconds=1)
    assert uow.sent.scans == []  # played on the spot, not sent to a worker


def test_loading_again_plays_only_what_is_new() -> None:
    load, _, scans = loader()
    load.load(OWNER, BRANDS, PLAN[1:])
    later = load.load(OWNER, BRANDS, PLAN)  # a longer recording: one scan is new
    assert later == {Played.ALREADY: 2, Played.PLAYED: 1} and len(scans.ran) == 3


def test_a_brand_that_isnt_the_owner_s_or_is_busy_is_not_played() -> None:
    load, uow, scans = loader()
    stranger = RecordedScan("rapido", T0, SETTINGS)
    assert load.load(OWNER, {"rapido": uuid.uuid4()}, [stranger]) == {Played.MISSING: 1}
    assert load.load(OWNER, {}, [stranger]) == {Played.MISSING: 1}
    load.load(OWNER, BRANDS, [PLAN[2]])
    (first,) = uow.scans.scans
    uow.scans.status[first] = ScanStatus.RUNNING  # a Scan now still running
    assert load.load(OWNER, BRANDS, [PLAN[0]]) == {Played.BUSY: 1}
    assert len(scans.ran) == 1


def test_the_replay_clock_reads_on_from_the_time_set() -> None:
    clock = ReplayClock()
    clock.set(T0)
    first, second = clock.now(), clock.now()
    assert T0 < first < second < T0 + timedelta(milliseconds=3)
    clock.set(T0 - HALF_DAY)  # it can be set back
    assert clock.now() < T0
    with pytest.raises(ValueError, match="aware"):
        clock.set(datetime(2026, 10, 3))  # noqa: DTZ001 (a naive time, refused)

"""Schedule slots aligned to local midnight, and quiet hours (data-model §2)."""

from datetime import UTC, datetime, time, timedelta

import pytest

from serpsense.domain.schedule import INTERVALS_MINUTES, InvalidSchedule, Schedule

pytestmark = pytest.mark.unit

IST_NOON = datetime(2026, 10, 3, 6, 30, tzinfo=UTC)  # 12:00 IST


@pytest.mark.parametrize(
    ("interval", "slot_ist"),
    [
        (60, "12:00"),
        (180, "12:00"),
        (360, "12:00"),
        (720, "12:00"),
        (1440, "00:00"),
    ],
)
def test_slots_align_to_local_midnight(interval: int, slot_ist: str) -> None:
    slot = Schedule(interval).slot(IST_NOON + timedelta(minutes=17))
    assert slot.tzinfo is UTC
    ist = slot + timedelta(hours=5, minutes=30)
    assert f"{ist:%H:%M}" == slot_ist


def test_every_time_in_a_slot_gives_the_same_start() -> None:
    schedule = Schedule(360)
    start = schedule.slot(IST_NOON)
    assert {schedule.slot(start + timedelta(minutes=m)) for m in (0, 1, 180, 359)} == {start}
    assert schedule.slot(start + timedelta(minutes=360)) == start + timedelta(hours=6)


def test_a_utc_brand_aligns_to_utc_midnight() -> None:
    at = datetime(2026, 10, 3, 13, 5, tzinfo=UTC)
    assert Schedule(720, timezone="UTC").slot(at) == datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "end", "ist", "quiet"),
    [
        (time(0), time(6), time(3), True),
        (time(0), time(6), time(6), False),  # end is exclusive
        (time(23), time(6), time(23, 30), True),  # overnight
        (time(23), time(6), time(5, 59), True),
        (time(23), time(6), time(12), False),
    ],
)
def test_quiet_hours(start: time, end: time, ist: time, quiet: bool) -> None:
    at = datetime(2026, 10, 3, ist.hour, ist.minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)
    assert Schedule(360, quiet_start=start, quiet_end=end).is_quiet(at) is quiet


def test_no_quiet_hours_is_never_quiet() -> None:
    assert Schedule(60).is_quiet(IST_NOON) is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval_minutes": 45},
        {"interval_minutes": 60, "quiet_start": time(1)},
        {"interval_minutes": 60, "quiet_start": time(1), "quiet_end": time(1)},
        {"interval_minutes": 60, "timezone": "Mars/Olympus"},
        {"interval_minutes": 60, "timezone": "America"},  # a directory of zones, not a zone
        {"interval_minutes": 60, "timezone": ""},
    ],
)
def test_invalid_schedules_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(InvalidSchedule):
        Schedule(**kwargs)  # type: ignore[arg-type]  # deliberately invalid input


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Schedule(60).slot(datetime(2026, 10, 3))  # noqa: DTZ001  # deliberately naive


# A UTC time shortly before a clock change in each zone.
CLOCK_CHANGES = [
    ("America/New_York", datetime(2026, 11, 1, 0, tzinfo=UTC)),  # clocks go back
    ("America/New_York", datetime(2026, 3, 8, 0, tzinfo=UTC)),  # clocks go forward
    ("Europe/London", datetime(2026, 10, 25, 0, tzinfo=UTC)),
    ("Pacific/Chatham", datetime(2026, 9, 26, 0, tzinfo=UTC)),  # +12:45 → +13:45
]


@pytest.mark.parametrize(("zone", "start"), CLOCK_CHANGES)
@pytest.mark.parametrize("interval", sorted(INTERVALS_MINUTES))
def test_a_clock_change_never_moves_a_slot_after_its_time(
    zone: str, start: datetime, interval: int
) -> None:
    schedule = Schedule(interval, timezone=zone)
    for minute in range(0, 48 * 60, 7):
        at = start + timedelta(minutes=minute)
        slot = schedule.slot(at)
        assert slot <= at < slot + timedelta(minutes=interval)
        assert schedule.slot(slot) == slot


@pytest.mark.parametrize(
    ("midnight", "hours"),
    [
        (datetime(2026, 11, 1, 4, tzinfo=UTC), 25),  # the repeated hour gets its own slot
        (datetime(2026, 3, 8, 5, tzinfo=UTC), 23),
    ],
)
def test_an_hourly_schedule_has_a_slot_for_every_hour_of_the_day(
    midnight: datetime, hours: int
) -> None:
    schedule = Schedule(60, timezone="America/New_York")
    day = [midnight + timedelta(minutes=m) for m in range(0, hours * 60, 10)]
    assert len({schedule.slot(at) for at in day}) == hours

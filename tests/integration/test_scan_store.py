"""The scan store on real Postgres: creation, compare-and-set moves and the sweep's queries."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, select

from serpsense.adapters.db.scan_store import SqlScanStore
from serpsense.domain.enums import ScanStatus, ScanTrigger, Surface, SurfaceOutcome, TransitionActor
from serpsense.domain.scan_state import IllegalTransition, Transition, TransitionReason
from serpsense.ports.scan_store import NewScan, SurfaceResult
from tests.integration.db_helpers import NOW, add_owned_brand, table

pytestmark = pytest.mark.integration

SCANS, TRANSITIONS = table("scans"), table("scan_status_transitions")
CLAIM = Transition(ScanStatus.QUEUED, ScanStatus.RUNNING, TransitionReason.CLAIMED)
DONE = Transition(ScanStatus.RUNNING, ScanStatus.SUCCEEDED, TransitionReason.COMPLETED)


@pytest.fixture
def store(conn: Connection) -> SqlScanStore:
    return SqlScanStore(conn)


def scheduled(brand_id: uuid.UUID, **overrides: Any) -> NewScan:
    values: dict[str, Any] = {
        "brand_id": brand_id,
        "trigger": ScanTrigger.SCHEDULE,
        "scheduled_for": NOW,
        "settings_snapshot": {"preset": "lean"},
        "estimated_searches": 6,
        "created_at": NOW,
    }
    return NewScan(**{**values, **overrides})


def history(conn: Connection, scan_id: uuid.UUID) -> list[tuple[Any, ...]]:
    columns = (TRANSITIONS.c.from_status, TRANSITIONS.c.to_status, TRANSITIONS.c.reason)
    rows = conn.execute(
        select(*columns).where(TRANSITIONS.c.scan_id == scan_id).order_by(TRANSITIONS.c.at)
    )
    return [tuple(row) for row in rows]


def assert_status_matches_history(conn: Connection) -> None:
    """`scans.status` is a named exception to "no derived values": it always equals the
    latest transition's `to_status`, the one no other transition of the scan continues from
    (no scan reaches a status twice, so it is unique even when two share a time)."""
    later = TRANSITIONS.alias("later")
    continued = (
        select(later.c.id)
        .where(later.c.scan_id == TRANSITIONS.c.scan_id)
        .where(later.c.from_status == TRANSITIONS.c.to_status)
    )
    latest = select(TRANSITIONS.c.scan_id, TRANSITIONS.c.to_status).where(~continued.exists())
    statuses = conn.execute(select(SCANS.c.id, SCANS.c.status)).all()
    assert sorted(map(tuple, statuses)) == sorted(map(tuple, conn.execute(latest).all()))


def test_a_scheduled_scan_is_created_queued_with_its_first_transition(
    conn: Connection, store: SqlScanStore
) -> None:
    scan_id = store.create(scheduled(add_owned_brand(conn)))
    assert scan_id is not None
    status = conn.execute(select(SCANS.c.status).where(SCANS.c.id == scan_id)).scalar_one()
    assert status == ScanStatus.QUEUED
    assert history(conn, scan_id) == [(None, ScanStatus.QUEUED, "scheduled")]


def test_a_taken_slot_or_an_active_scan_creates_nothing(
    conn: Connection, store: SqlScanStore
) -> None:
    brand_id = add_owned_brand(conn)
    owner = conn.execute(select(table("brands").c.owner_id)).scalar_one()
    first = store.create(scheduled(brand_id))
    assert store.create(scheduled(brand_id)) is None  # the same slot
    manual = scheduled(brand_id, trigger=ScanTrigger.MANUAL, scheduled_for=None, requested_by=owner)
    assert store.create(manual) is None  # "Scan now" while a scan is active
    assert conn.execute(select(SCANS.c.id)).scalars().all() == [first]
    assert len(conn.execute(select(TRANSITIONS.c.id)).all()) == 1
    assert_status_matches_history(conn)


def test_scan_now_is_recorded_as_the_users_request(conn: Connection, store: SqlScanStore) -> None:
    brand_id = add_owned_brand(conn)
    owner = conn.execute(select(table("brands").c.owner_id)).scalar_one()
    manual = scheduled(brand_id, trigger=ScanTrigger.MANUAL, scheduled_for=None, requested_by=owner)
    scan_id = store.create(manual)
    actor = conn.execute(
        select(TRANSITIONS.c.actor, TRANSITIONS.c.actor_user_id, TRANSITIONS.c.reason).where(
            TRANSITIONS.c.scan_id == scan_id
        )
    ).one()
    assert tuple(actor) == (TransitionActor.USER, owner, "requested")


def test_a_replay_scan_is_created_by_the_system_and_its_time_is_known(
    conn: Connection, store: SqlScanStore
) -> None:
    brand_id = add_owned_brand(conn)
    replay = scheduled(brand_id, trigger=ScanTrigger.REPLAY, scheduled_for=None)
    scan_id = store.create(replay)
    assert history(conn, scan_id) == [(None, "queued", "replayed")]
    archived = Transition(ScanStatus.QUEUED, ScanStatus.SKIPPED, TransitionReason.BRAND_ARCHIVED)
    store.move(scan_id, archived, at=NOW)
    later = scheduled(brand_id, created_at=NOW + timedelta(hours=1), scheduled_for=NOW)
    store.create(later)  # a scheduled scan is no replayed one
    assert store.replayed_at(brand_id) == {NOW}
    assert store.replayed_at(add_owned_brand(conn, slug="other")) == frozenset()


def test_a_redelivered_claim_is_a_no_op(conn: Connection, store: SqlScanStore) -> None:
    brand_id = add_owned_brand(conn)
    scan_id = store.create(scheduled(brand_id))
    assert scan_id is not None
    assert store.move(scan_id, CLAIM, at=NOW + timedelta(seconds=1)) is True
    assert store.move(scan_id, CLAIM, at=NOW + timedelta(seconds=2)) is False
    assert store.move(scan_id, DONE, at=NOW + timedelta(minutes=3)) is True
    assert history(conn, scan_id) == [
        (None, ScanStatus.QUEUED, "scheduled"),
        (ScanStatus.QUEUED, ScanStatus.RUNNING, "claimed"),
        (ScanStatus.RUNNING, ScanStatus.SUCCEEDED, "completed"),
    ]
    assert store.create(scheduled(brand_id)) is None  # a finished slot never runs again
    later = scheduled(brand_id, scheduled_for=NOW + timedelta(hours=12))
    assert store.create(later) is not None  # no longer active, so the next slot can start
    assert_status_matches_history(conn)


def test_a_creation_is_never_a_move(conn: Connection, store: SqlScanStore) -> None:
    scan_id = store.create(scheduled(add_owned_brand(conn)))
    assert scan_id is not None
    with pytest.raises(IllegalTransition, match="created"):
        store.move(scan_id, Transition(None, ScanStatus.QUEUED, TransitionReason.SCHEDULED), at=NOW)


def test_the_sweep_finds_lost_and_stuck_scans(conn: Connection, store: SqlScanStore) -> None:
    lost = store.create(scheduled(add_owned_brand(conn, "ola")))
    fresh = store.create(
        scheduled(add_owned_brand(conn, "uber"), created_at=NOW + timedelta(minutes=5))
    )
    stuck = store.create(scheduled(add_owned_brand(conn, "rapido")))
    assert lost and fresh and stuck
    store.move(stuck, CLAIM, at=NOW)
    cutoff = NOW + timedelta(minutes=2)
    assert store.queued_before(cutoff) == [lost]
    assert store.running_before(cutoff) == [stuck]
    assert store.running_before(NOW) == []
    assert_status_matches_history(conn)


def test_a_scans_surface_results_are_written_once(conn: Connection) -> None:
    store = SqlScanStore(conn)
    scan_id = store.create(scheduled(add_owned_brand(conn)))
    assert scan_id is not None
    first = [
        SurfaceResult(Surface.SEARCH_PAGE, SurfaceOutcome.SUCCEEDED),
        SurfaceResult(Surface.NEWS, SurfaceOutcome.FAILED, "serpapi.http_5xx"),
    ]
    assert store.record_surfaces(scan_id, first) == 2
    again = [
        SurfaceResult(Surface.NEWS, SurfaceOutcome.SUCCEEDED),  # a retry keeps the first
        SurfaceResult(Surface.MAPS, SurfaceOutcome.DISABLED),
    ]
    assert store.record_surfaces(scan_id, again) == 1
    assert store.record_surfaces(scan_id, []) == 0
    results = table("scan_surface_results")
    rows = conn.execute(select(results.c.surface, results.c.outcome, results.c.error_code))
    assert sorted(tuple(row) for row in rows) == [
        ("maps", "disabled", None),
        ("news", "failed", "serpapi.http_5xx"),
        ("search_page", "succeeded", None),
    ]

"""Scans under concurrency, with two real connections (AGENTS §9, ADR-0005).

The first transaction writes and stays open; the second runs the competing write in a thread,
and the first commits only once Postgres reports the second waiting on its lock, so the race
really happens.
"""

import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

import pytest
from sqlalchemy import Connection, Engine, text

from serpsense.adapters.db.scan_store import SqlScanStore
from serpsense.domain.enums import ScanStatus, ScanTrigger
from serpsense.domain.scan_state import Transition, TransitionReason
from serpsense.ports.scan_store import NewScan
from tests.integration.db_helpers import NOW, add_owned_brand

pytestmark = pytest.mark.integration

T = TypeVar("T")
CLAIM = Transition(ScanStatus.QUEUED, ScanStatus.RUNNING, TransitionReason.CLAIMED)
ARCHIVED = Transition(ScanStatus.QUEUED, ScanStatus.SKIPPED, TransitionReason.BRAND_ARCHIVED)


def scheduled(engine: Engine) -> NewScan:
    with engine.begin() as conn:
        brand_id = add_owned_brand(conn, f"brand-{uuid.uuid4().hex[:12]}")
    return NewScan(brand_id, ScanTrigger.SCHEDULE, {}, 6, NOW, scheduled_for=NOW)


def race(
    engine: Engine, first: Callable[[Connection], Any], second: Callable[[Connection], T]
) -> T:
    """Run `first` and keep it uncommitted; run `second` on another connection until Postgres
    reports it blocked by `first`; commit `first`; return what `second` got. The engine's lock
    timeout bounds every wait, so a broken test fails instead of hanging."""
    with engine.connect() as a, engine.connect() as b, ThreadPoolExecutor(1) as pool:
        with a.begin():
            first(a)
            holder = a.execute(text("SELECT pg_backend_pid()")).scalar_one()
            waiter = b.execute(text("SELECT pg_backend_pid()")).scalar_one()
            b.commit()  # end the transaction that query began, so the thread can begin its own
            pending = pool.submit(_in_transaction, b, second)
            _wait_until_blocked(engine, waiter, holder, pending)
        return pending.result(timeout=30)


def _in_transaction(conn: Connection, work: Callable[[Connection], T]) -> T:
    with conn.begin():
        return work(conn)


def _wait_until_blocked(engine: Engine, waiter: int, holder: int, pending: Future[Any]) -> None:
    query = text("SELECT CAST(:holder AS integer) = ANY(pg_blocking_pids(:waiter))")
    deadline = time.monotonic() + 10
    with engine.connect() as watcher:
        while not watcher.execute(query, {"holder": holder, "waiter": waiter}).scalar():
            if pending.done():
                pending.result()  # surfaces the second transaction's own error
                raise AssertionError("the second transaction finished without waiting")
            assert time.monotonic() < deadline, "the second transaction never waited"


def test_two_dispatchers_create_one_scan_for_a_slot(committing_engine: Engine) -> None:
    scan = scheduled(committing_engine)
    created: list[uuid.UUID | None] = []

    def first(conn: Connection) -> None:
        """Create the slot's scan, then end it, so only the slot rule can stop the second."""
        store = SqlScanStore(conn)
        created.append(scan_id := store.create(scan))
        assert scan_id is not None and store.move(scan_id, ARCHIVED, at=NOW)

    second = race(committing_engine, first, lambda conn: SqlScanStore(conn).create(scan))
    assert created[0] is not None and second is None


def test_scan_now_waits_for_and_yields_to_the_scheduled_scan(committing_engine: Engine) -> None:
    scan = scheduled(committing_engine)
    with committing_engine.begin() as conn:
        owner = conn.execute(
            text("SELECT owner_id FROM brands WHERE id = :id"), {"id": scan.brand_id}
        ).scalar_one()
    manual = NewScan(scan.brand_id, ScanTrigger.MANUAL, {}, 6, NOW, requested_by=owner)
    second = race(
        committing_engine,
        lambda conn: SqlScanStore(conn).create(scan),
        lambda conn: SqlScanStore(conn).create(manual),
    )
    assert second is None  # one active scan per brand


def test_a_redelivered_claim_racing_the_first_is_a_no_op(committing_engine: Engine) -> None:
    with committing_engine.begin() as conn:
        scan_id = SqlScanStore(conn).create(scheduled(committing_engine))
    assert scan_id is not None
    claimed: list[bool] = []
    second = race(
        committing_engine,
        lambda conn: claimed.append(SqlScanStore(conn).move(scan_id, CLAIM, at=NOW)),
        lambda conn: SqlScanStore(conn).move(scan_id, CLAIM, at=NOW),
    )
    assert claimed == [True] and second is False

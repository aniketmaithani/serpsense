"""The unit of work on real Postgres: one transaction, and jobs sent only after it commits."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select, text
from structlog.testing import capture_logs

from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.domain.enums import ScanTrigger
from serpsense.ports.job_queue import JobQueueUnavailable
from serpsense.ports.scan_store import NewScan
from serpsense.ports.unit_of_work import UnitOfWork
from tests.integration.db_helpers import NOW, add_owned_brand, table

pytestmark = pytest.mark.integration

SCANS = table("scans")


class RecordingQueue:
    """Checks, as a worker would, that each scan it is sent is already committed."""

    def __init__(self, engine: Engine, *, available: bool = True) -> None:
        self.engine, self.available = engine, available
        self.sent: list[uuid.UUID] = []

    def run_scan(self, scan_id: uuid.UUID) -> None:
        if not self.available:
            raise JobQueueUnavailable
        with self.engine.connect() as conn:
            assert conn.execute(select(SCANS.c.id).where(SCANS.c.id == scan_id)).one()
        self.sent.append(scan_id)


def new_scan(engine: Engine) -> NewScan:
    with engine.begin() as conn:
        brand_id = add_owned_brand(conn, f"brand-{uuid.uuid4().hex[:12]}")
    return NewScan(brand_id, ScanTrigger.SCHEDULE, {}, 6, NOW, scheduled_for=NOW)


def exists(engine: Engine, scan_id: uuid.UUID | None) -> bool:
    with engine.connect() as conn:
        return conn.execute(select(SCANS.c.id).where(SCANS.c.id == scan_id)).first() is not None


def test_a_clean_block_commits_and_then_sends_its_jobs(committing_engine: Engine) -> None:
    queue = RecordingQueue(committing_engine)
    uow: UnitOfWork = SqlUnitOfWork(committing_engine, queue)
    with uow:
        scan_id = uow.scans.create(new_scan(committing_engine))
        assert scan_id is not None
        uow.jobs.run_scan(scan_id)
        assert queue.sent == [] and not exists(committing_engine, scan_id)  # nothing yet
    assert queue.sent == [scan_id]


def test_a_block_that_raises_rolls_back_and_sends_nothing(committing_engine: Engine) -> None:
    queue = RecordingQueue(committing_engine)
    uow = SqlUnitOfWork(committing_engine, queue)
    scan_id = None
    with pytest.raises(LookupError), uow:
        scan_id = uow.scans.create(new_scan(committing_engine))
        uow.jobs.run_scan(scan_id or uuid.uuid4())
        raise LookupError
    assert scan_id is not None and not exists(committing_engine, scan_id)
    assert queue.sent == []


def test_a_broker_outage_leaves_the_scan_queued_for_the_sweep(committing_engine: Engine) -> None:
    uow = SqlUnitOfWork(committing_engine, RecordingQueue(committing_engine, available=False))
    with capture_logs() as logs, uow:
        scan_id = uow.scans.create(new_scan(committing_engine))
        uow.jobs.run_scan(scan_id or uuid.uuid4())
    assert exists(committing_engine, scan_id)
    assert {"event": "job.enqueue_failed", "job": "run_scan", "scan_id": str(scan_id)} in [
        {k: v for k, v in entry.items() if k != "log_level"} for entry in logs
    ]


def test_a_unit_of_work_is_not_reentrant(committing_engine: Engine) -> None:
    uow = SqlUnitOfWork(committing_engine, RecordingQueue(committing_engine))
    with uow:
        scan_id = uow.scans.create(new_scan(committing_engine))
        with pytest.raises(RuntimeError, match="reentrant"), uow:
            pass
    assert exists(committing_engine, scan_id)  # the outer transaction survived and committed
    with uow:  # once closed, it opens again
        assert scan_id in uow.scans.queued_before(NOW + timedelta(minutes=1))


def test_a_job_asked_for_after_the_block_raises(committing_engine: Engine) -> None:
    uow = SqlUnitOfWork(committing_engine, RecordingQueue(committing_engine))
    with uow:
        jobs = uow.jobs
    with pytest.raises(RuntimeError, match="ended"):
        jobs.run_scan(uuid.uuid4())


def test_sessions_bound_lock_waits_and_idle_transactions(committing_engine: Engine) -> None:
    with committing_engine.connect() as conn:
        settings = conn.execute(
            select(
                text("current_setting('lock_timeout')"),
                text("current_setting('idle_in_transaction_session_timeout')"),
            )
        ).one()
    assert tuple(settings) == ("15s", "1min")

"""One Postgres transaction per unit of work, with jobs sent after it commits (ADR-0005)."""

import uuid
from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine, RootTransaction

from serpsense.adapters.db.accounts import SqlAccounts
from serpsense.adapters.db.brand_store import SqlBrandStore
from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.adapters.db.mention_store import SqlMentionStore
from serpsense.adapters.db.observation_store import SqlObservationStore
from serpsense.adapters.db.scan_store import SqlScanStore
from serpsense.adapters.db.scan_targets import SqlScanTargets
from serpsense.adapters.db.scheduled_brands import SqlScheduledBrands
from serpsense.adapters.db.score_store import SqlScoreStore
from serpsense.observability import get_logger
from serpsense.ports.accounts import Accounts
from serpsense.ports.brand_store import BrandStore
from serpsense.ports.enrichment_store import EnrichmentStore
from serpsense.ports.job_queue import JobQueue, JobQueueUnavailable
from serpsense.ports.mention_store import MentionStore
from serpsense.ports.observation_store import ObservationStore
from serpsense.ports.scan_store import ScanStore
from serpsense.ports.scan_targets import ScanTargets
from serpsense.ports.scheduled_brands import ScheduledBrands
from serpsense.ports.score_store import ScoreStore

log = get_logger(__name__)


class _AfterCommit:
    """Holds the jobs a unit of work asks for until it commits; closed once the block ends, so
    a job asked for too late raises instead of being dropped."""

    def __init__(self) -> None:
        self.scans: list[uuid.UUID] = []
        self.closed = False

    def run_scan(self, scan_id: uuid.UUID) -> None:
        if self.closed:
            raise RuntimeError("the unit of work has ended; open a new one")
        self.scans.append(scan_id)


class SqlUnitOfWork:
    """One transaction per `with` block; entering a unit of work that is already open raises,
    so a nested block can never abandon a transaction holding locks."""

    scans: ScanStore
    mentions: MentionStore
    observations: ObservationStore
    schedules: ScheduledBrands
    enrichments: EnrichmentStore
    targets: ScanTargets
    brands: BrandStore
    accounts: Accounts
    scores: ScoreStore
    jobs: JobQueue

    def __init__(self, engine: Engine, queue: JobQueue) -> None:
        self._engine = engine
        self._queue = queue
        self._open = False

    def __enter__(self) -> Self:
        if self._open:
            raise RuntimeError("a unit of work is not reentrant; open a new one")
        self._conn: Connection = self._engine.connect()
        self._transaction: RootTransaction = self._conn.begin()
        self._open = True
        self._pending = _AfterCommit()
        self.scans = SqlScanStore(self._conn)
        self.mentions = SqlMentionStore(self._conn)
        self.observations = SqlObservationStore(self._conn)
        self.schedules = SqlScheduledBrands(self._conn)
        self.enrichments = SqlEnrichmentStore(self._conn)
        self.targets = SqlScanTargets(self._conn)
        self.brands = SqlBrandStore(self._conn)
        self.accounts = SqlAccounts(self._conn)
        self.scores = SqlScoreStore(self._conn)
        self.jobs = self._pending
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                self._transaction.commit()
            else:
                self._transaction.rollback()
        finally:
            self._conn.close()
            self._pending.closed = True
            self._open = False
        if exc_type is None:
            self._send(self._pending)

    def _send(self, pending: _AfterCommit) -> None:
        for scan_id in pending.scans:
            try:
                self._queue.run_scan(scan_id)
            except JobQueueUnavailable:
                # Committed already: the scan stays queued and the sweep sends it again.
                log.warning("job.enqueue_failed", job="run_scan", scan_id=str(scan_id))

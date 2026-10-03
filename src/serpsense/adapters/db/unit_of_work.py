"""One Postgres transaction per unit of work, with jobs sent after it commits (ADR-0005)."""

import uuid
from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine, RootTransaction
from sqlalchemy.exc import DBAPIError

from serpsense.adapters.db.accounts import SqlAccounts
from serpsense.adapters.db.alert_store import SqlAlertStore
from serpsense.adapters.db.audit import SqlAuditLog
from serpsense.adapters.db.brand_store import SqlBrandStore
from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.adapters.db.mention_store import SqlMentionStore
from serpsense.adapters.db.narrative_store import SqlNarrativeStore
from serpsense.adapters.db.observation_store import SqlObservationStore
from serpsense.adapters.db.otp_codes import SqlOtpCodes
from serpsense.adapters.db.outbox import SqlOutbox
from serpsense.adapters.db.scan_store import SqlScanStore
from serpsense.adapters.db.scan_targets import SqlScanTargets
from serpsense.adapters.db.scheduled_brands import SqlScheduledBrands
from serpsense.adapters.db.score_store import SqlScoreStore
from serpsense.adapters.db.sessions import SqlSessions
from serpsense.observability import get_logger
from serpsense.ports.accounts import Accounts
from serpsense.ports.alert_store import AlertStore
from serpsense.ports.audit import AuditLog
from serpsense.ports.brand_store import BrandStore
from serpsense.ports.enrichment_store import EnrichmentStore
from serpsense.ports.job_queue import JobQueue, JobQueueUnavailable
from serpsense.ports.mention_store import MentionStore
from serpsense.ports.narrative_store import NarrativeStore
from serpsense.ports.observation_store import ObservationStore
from serpsense.ports.otp_codes import OtpCodes
from serpsense.ports.outbox import Outbox
from serpsense.ports.scan_store import ScanStore
from serpsense.ports.scan_targets import ScanTargets
from serpsense.ports.scheduled_brands import ScheduledBrands
from serpsense.ports.score_store import ScoreStore
from serpsense.ports.sessions import Sessions
from serpsense.ports.unit_of_work import Busy

log = get_logger(__name__)

LOCK_NOT_AVAILABLE = "55P03"  # the session's lock_timeout ran out (adapters/db/engine.py)


class _AfterCommit:
    """Holds the jobs a unit of work asks for until it commits; closed once the block ends, so
    a job asked for too late raises instead of being dropped."""

    def __init__(self) -> None:
        self.scans: list[uuid.UUID] = []
        self.nudge_outbox = False
        self.closed = False

    def run_scan(self, scan_id: uuid.UUID) -> None:
        self._open()
        self.scans.append(scan_id)

    def dispatch_outbox(self) -> None:
        self._open()
        self.nudge_outbox = True

    def _open(self) -> None:
        if self.closed:
            raise RuntimeError("the unit of work has ended; open a new one")


class SqlUnitOfWork:
    """One transaction per `with` block; entering a unit of work that is already open raises,
    so a nested block can never abandon a transaction holding locks."""

    scans: ScanStore
    mentions: MentionStore
    observations: ObservationStore
    schedules: ScheduledBrands
    enrichments: EnrichmentStore
    narratives: NarrativeStore
    targets: ScanTargets
    brands: BrandStore
    accounts: Accounts
    otp_codes: OtpCodes
    sessions: Sessions
    audit: AuditLog
    outbox: Outbox
    scores: ScoreStore
    alerts: AlertStore
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
        self.narratives = SqlNarrativeStore(self._conn)
        self.targets = SqlScanTargets(self._conn)
        self.brands = SqlBrandStore(self._conn)
        self.accounts = SqlAccounts(self._conn)
        self.otp_codes = SqlOtpCodes(self._conn)
        self.sessions = SqlSessions(self._conn)
        self.audit = SqlAuditLog(self._conn)
        self.outbox = SqlOutbox(self._conn)
        self.scores = SqlScoreStore(self._conn)
        self.alerts = SqlAlertStore(self._conn)
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
        elif _lock_wait_ran_out(exc):
            raise Busy("a row stayed locked past the lock timeout; nothing was changed") from exc

    def _send(self, pending: _AfterCommit) -> None:
        for scan_id in pending.scans:
            try:
                self._queue.run_scan(scan_id)
            except JobQueueUnavailable:
                # Committed already: the scan stays queued and the sweep sends it again.
                log.warning("job.enqueue_failed", job="run_scan", scan_id=str(scan_id))
        if pending.nudge_outbox:
            try:
                self._queue.dispatch_outbox()
            except JobQueueUnavailable:  # Beat runs the dispatcher within 15 seconds anyway
                log.warning("job.enqueue_failed", job="dispatch_outbox")


def _lock_wait_ran_out(exc: BaseException | None) -> bool:
    return isinstance(exc, DBAPIError) and (
        getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE
    )

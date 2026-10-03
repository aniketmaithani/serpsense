"""Port for one database transaction and the work it hands on (ADR-0005, AGENTS §5).

Stores write through the unit of work: it commits when its block ends cleanly and rolls back when
the block raises, so a scan's finish commits together with the alerts and emails it caused.
Jobs asked for inside it are sent only after it commits, so a worker never looks for a row that
isn't there yet, and a rolled-back change sends nothing.

A lock wait that outlasts the session's lock timeout ends the unit of work with `Busy`, so a
caller can tell "try again shortly" from a fault.

Keep units of work short: no SerpApi, LLM, SMTP or broker call inside one. A session that sits
idle in a transaction for a minute is ended by Postgres, so a service opens one unit of work
per stage and does its slow calls between them.
"""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self

from serpsense.ports.accounts import Accounts
from serpsense.ports.alert_store import AlertStore
from serpsense.ports.audit import AuditLog
from serpsense.ports.brand_store import BrandStore
from serpsense.ports.drafts import DraftStore
from serpsense.ports.enrichment_store import EnrichmentStore
from serpsense.ports.explanations import ExplanationStore
from serpsense.ports.job_queue import JobQueue
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


class Busy(RuntimeError):
    """A row the unit of work needed stayed locked past the session's lock timeout (another
    transaction held it, say an email being sent). Everything was rolled back; trying again
    shortly may work."""


class UnitOfWork(Protocol):
    # Read-only for callers, so an implementation (or a fake) may hold any store that fits.
    @property
    def scans(self) -> ScanStore: ...

    @property
    def mentions(self) -> MentionStore: ...

    @property
    def observations(self) -> ObservationStore: ...

    @property
    def schedules(self) -> ScheduledBrands: ...

    @property
    def enrichments(self) -> EnrichmentStore: ...

    @property
    def narratives(self) -> NarrativeStore: ...

    @property
    def targets(self) -> ScanTargets: ...

    @property
    def brands(self) -> BrandStore: ...

    @property
    def accounts(self) -> Accounts: ...

    @property
    def otp_codes(self) -> OtpCodes: ...

    @property
    def sessions(self) -> Sessions: ...

    @property
    def audit(self) -> AuditLog: ...

    @property
    def outbox(self) -> Outbox: ...

    @property
    def scores(self) -> ScoreStore: ...

    @property
    def alerts(self) -> AlertStore: ...

    @property
    def explanations(self) -> ExplanationStore: ...

    @property
    def drafts(self) -> DraftStore: ...

    @property
    def jobs(self) -> JobQueue:
        """Held until the commit; asking after the block ends raises."""
        ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


# Opens a new unit of work; services take one so each use is its own transaction.
UnitOfWorkFactory = Callable[[], UnitOfWork]

"""In-memory fakes behind the ports, for service tests (AGENTS §9: no network, no database)."""

import uuid
from collections.abc import Callable, Collection, Sequence
from datetime import datetime
from types import TracebackType
from typing import Self

from serpsense.domain.enums import MentionSource, ScanStatus
from serpsense.domain.llm_pricing import Hop, TokenUsage
from serpsense.domain.observation import AppRating
from serpsense.domain.scan_state import ACTIVE, IllegalTransition, Transition
from serpsense.ports.enrichment_store import MentionLabel, PendingText
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse
from serpsense.ports.llm_ledger import LlmCallRecord
from serpsense.ports.mention_store import Recorded, Sighting
from serpsense.ports.observation_store import Comparison
from serpsense.ports.scan_store import NewScan
from serpsense.ports.scheduled_brands import ScheduledBrand


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class InMemoryScans:
    """Keeps the database's two rules: one scan per slot, one active scan per brand."""

    def __init__(self) -> None:
        self.scans: dict[uuid.UUID, NewScan] = {}
        self.status: dict[uuid.UUID, ScanStatus] = {}
        self.transitions: list[tuple[uuid.UUID, Transition, datetime]] = []

    def create(self, scan: NewScan) -> uuid.UUID | None:
        same_brand = [i for i, s in self.scans.items() if s.brand_id == scan.brand_id]
        if any(self.status[i] in ACTIVE for i in same_brand) or any(
            scan.scheduled_for is not None and self.scans[i].scheduled_for == scan.scheduled_for
            for i in same_brand
        ):
            return None
        scan_id = uuid.uuid4()
        self.scans[scan_id], self.status[scan_id] = scan, ScanStatus.QUEUED
        return scan_id

    def move(self, scan_id: uuid.UUID, transition: Transition, *, at: datetime) -> bool:
        if transition.from_status is None:
            raise IllegalTransition("a scan is created, never moved into existence")
        if self.status.get(scan_id) is not transition.from_status:
            return False
        self.status[scan_id] = transition.to_status
        self.transitions.append((scan_id, transition, at))
        return True

    def queued_before(self, at: datetime) -> Sequence[uuid.UUID]:
        return [
            i
            for i, s in self.scans.items()
            if self.status[i] is ScanStatus.QUEUED and s.created_at < at
        ]

    def running_before(self, at: datetime) -> Sequence[uuid.UUID]:
        return [
            i
            for i, t, when in self.transitions
            if t.to_status is ScanStatus.RUNNING
            and when < at
            and self.status[i] is ScanStatus.RUNNING
        ]


class StaticSchedules:
    def __init__(self, brands: Sequence[ScheduledBrand]) -> None:
        self.brands = brands

    def scheduled_brands(self, as_of: datetime) -> Sequence[ScheduledBrand]:
        self.read_as_of = as_of
        return self.brands


class RecordingMentions:
    def __init__(self) -> None:
        self.sightings: list[Sighting] = []

    def record(self, sighting: Sighting, *, at: datetime) -> Recorded:
        self.sightings.append(sighting)
        seen = len(sighting.mentions)
        return Recorded(new=seen, revised=0, observed=seen)


class RecordingObservations:
    def __init__(self) -> None:
        self.comparisons: list[Comparison] = []
        self.ratings: list[tuple[uuid.UUID, uuid.UUID, AppRating]] = []

    def record_trends(self, comparison: Comparison) -> int:
        self.comparisons.append(comparison)
        return len(comparison.points)

    def record_app_rating(
        self, scan_id: uuid.UUID, brand_app_id: uuid.UUID, rating: AppRating
    ) -> bool:
        self.ratings.append((scan_id, brand_app_id, rating))
        return True


class RecordingEnrichments:
    """Hands out preset pending texts and keeps the labels it is given."""

    def __init__(self, pending: Sequence[PendingText] = ()) -> None:
        self.waiting = list(pending)
        self.labels: list[tuple[MentionLabel, str, uuid.UUID]] = []

    def pending(
        self,
        brand_id: uuid.UUID,
        *,
        prompt_version: str,
        sources: Collection[MentionSource],
        seen_since: datetime,
        limit: int,
    ) -> Sequence[PendingText]:
        done = {(lbl.mention_id, lbl.revision) for lbl, v, _ in self.labels if v == prompt_version}
        fits = [p for p in self.waiting if p.source in sources]
        return [p for p in fits if (p.mention_id, p.revision) not in done][:limit]

    def record(
        self,
        labels: Sequence[MentionLabel],
        *,
        prompt_version: str,
        llm_call_id: uuid.UUID,
        at: datetime,
    ) -> int:
        done = {(lbl.mention_id, lbl.revision, v) for lbl, v, _ in self.labels}
        new = [lbl for lbl in labels if (lbl.mention_id, lbl.revision, prompt_version) not in done]
        self.labels.extend((label, prompt_version, llm_call_id) for label in new)
        return len(new)  # a label already there stays, as in the table


class RecordingJobs:
    def __init__(self) -> None:
        self.scans: list[uuid.UUID] = []

    def run_scan(self, scan_id: uuid.UUID) -> None:
        self.scans.append(scan_id)


class FakeUnitOfWork:
    """Shares its stores across units of work; jobs reach `sent` only on a clean exit."""

    def __init__(self, scans: InMemoryScans, schedules: StaticSchedules) -> None:
        self.scans, self.schedules = scans, schedules
        self.mentions, self.observations = RecordingMentions(), RecordingObservations()
        self.enrichments = RecordingEnrichments()
        self.sent = RecordingJobs()
        self.jobs = RecordingJobs()

    def __call__(self) -> Self:
        return self

    def __enter__(self) -> Self:
        self.jobs = RecordingJobs()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.sent.scans.extend(self.jobs.scans)


Answerer = Callable[[LlmRequest], "str | LlmCallFailed"]


class ScriptedLlm:
    """Answers each request with the structured output a function gives (or raises it)."""

    def __init__(self, answer: Answerer, model: str = "claude-opus-5-5") -> None:
        self.answer, self.model = answer, model
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        output = self.answer(request)
        if isinstance(output, LlmCallFailed):
            raise output
        usage = TokenUsage(input=1000, output=200, cache_read=0, cache_write=0)
        return LlmResponse(self.model, "end_turn", output, (Hop(self.model, usage),), 900)


class MemoryLedger:
    """Keeps calls; `spent_after` sets the spend once a call is recorded (a budget used up)."""

    def __init__(self, spent: int = 0, spent_after: int | None = None) -> None:
        self.spent, self.spent_after = spent, spent_after
        self.calls: list[LlmCallRecord] = []

    def record(self, call: LlmCallRecord) -> uuid.UUID:
        self.calls.append(call)
        if self.spent_after is not None:
            self.spent = self.spent_after
        return uuid.UUID(int=len(self.calls))

    def spent_since(self, user_id: uuid.UUID, since: datetime) -> int:
        return self.spent

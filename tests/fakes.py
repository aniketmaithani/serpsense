"""In-memory fakes behind the ports, for service tests (AGENTS §9: no network, no database)."""

import uuid
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType, TracebackType
from typing import Any, Self

import structlog

from serpsense.domain.enums import (
    AlertRule,
    LlmTask,
    MentionSource,
    ScanStatus,
    SerpEngine,
    ServedFrom,
    Surface,
)
from serpsense.domain.llm_capabilities import TaskSettings
from serpsense.domain.llm_pricing import Hop, TokenUsage
from serpsense.domain.mention import ParsedMention, best_ranked, text_key
from serpsense.domain.observation import AppRating
from serpsense.domain.scan_state import ACTIVE, IllegalTransition, Transition
from serpsense.domain.scoring.scan import ScanScores, ScoreInputs
from serpsense.ports.alert_store import ScanAlertContext
from serpsense.ports.collector import Lead, Reading, Target
from serpsense.ports.enrichment_store import MentionLabel, PendingText
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse
from serpsense.ports.llm_ledger import LlmCallRecord
from serpsense.ports.mention_store import Recorded, Sighting
from serpsense.ports.observation_store import Comparison
from serpsense.ports.scan_store import NewScan, SurfaceResult
from serpsense.ports.scan_targets import ScanTarget
from serpsense.ports.scheduled_brands import ScanInputs, ScheduledBrand
from serpsense.ports.search_provider import SearchRequest
from serpsense.services.search import SearchResult


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class TickingClock:
    """Each reading of the time is the next one given; the last one stays."""

    def __init__(self, *times: datetime) -> None:
        self.times = list(times)

    def now(self) -> datetime:
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]


class InMemoryScans:
    """Keeps the database's two rules: one scan per slot, one active scan per brand."""

    def __init__(self) -> None:
        self.scans: dict[uuid.UUID, NewScan] = {}
        self.status: dict[uuid.UUID, ScanStatus] = {}
        self.transitions: list[tuple[uuid.UUID, Transition, datetime]] = []
        self.surfaces: dict[uuid.UUID, dict[Surface, SurfaceResult]] = {}

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

    def record_surfaces(self, scan_id: uuid.UUID, results: Sequence[SurfaceResult]) -> int:
        if scan_id not in self.scans:
            raise KeyError(scan_id)  # the table's foreign key
        kept, written = self.surfaces.setdefault(scan_id, {}), 0
        for result in results:
            if result.surface not in kept:
                kept[result.surface], written = result, written + 1
        return written

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


class StaticTargets:
    """Scan targets by scan id; change `targets` to archive a brand mid-scan."""

    def __init__(self, *targets: ScanTarget) -> None:
        self.targets = {target.scan_id: target for target in targets}

    def for_scan(self, scan_id: uuid.UUID) -> ScanTarget | None:
        return self.targets.get(scan_id)


class StaticSchedules:
    def __init__(
        self,
        brands: Sequence[ScheduledBrand] = (),
        owned: Mapping[tuple[uuid.UUID, uuid.UUID], ScanInputs] | None = None,
    ) -> None:
        self.brands = brands
        self.owned = owned or {}  # by (owner, brand): the brands "Scan now" may scan

    def scheduled_brands(self, as_of: datetime) -> Sequence[ScheduledBrand]:
        self.read_as_of = as_of
        return self.brands

    def scan_inputs(
        self, owner_id: uuid.UUID, brand_id: uuid.UUID, as_of: datetime
    ) -> ScanInputs | None:
        self.read_as_of = as_of
        return self.owned.get((owner_id, brand_id))


class RecordingMentions:
    """Keeps sightings and counts as the store does: a mention is new the first time it is seen,
    and observed once per scan, at its best rank."""

    def __init__(self) -> None:
        self.sightings: list[Sighting] = []
        self.known: set[tuple[MentionSource, str]] = set()
        self.observed: set[tuple[uuid.UUID, MentionSource, str]] = set()

    def record(self, sighting: Sighting, *, at: datetime) -> Recorded:
        self.sightings.append(sighting)
        seen = [(m.source, m.identity_key) for m in best_ranked(sighting.mentions)]
        new = [key for key in seen if key not in self.known]
        fresh = [(sighting.scan_id, *key) for key in seen]
        fresh = [key for key in fresh if key not in self.observed]
        self.known.update(seen)
        self.observed.update(fresh)
        return Recorded(new=len(new), revised=0, observed=len(fresh))


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
        self.asked_for: list[uuid.UUID] = []  # the brands whose pending texts were asked for

    def pending(
        self,
        brand_id: uuid.UUID,
        *,
        prompt_version: str,
        sources: Collection[MentionSource],
        seen_since: datetime,
        limit: int,
    ) -> Sequence[PendingText]:
        self.asked_for.append(brand_id)
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


class RecordingScores:
    """Hands out preset scoring inputs (nothing seen, by default) and keeps the scores given;
    both only inside a unit of work."""

    def __init__(self, inside: Callable[[], bool]) -> None:
        self.inside = inside
        self.given: ScoreInputs | None = None
        self.asked: list[tuple[uuid.UUID, dict[LlmTask, str]]] = []
        self.recorded: dict[uuid.UUID, tuple[ScanScores, str, datetime]] = {}
        self.backlog: list[uuid.UUID] = []  # finished scans with no scores, oldest first

    def inputs(self, scan_id: uuid.UUID, *, prompts: Mapping[LlmTask, str]) -> ScoreInputs:
        assert self.inside()
        self.asked.append((scan_id, dict(prompts)))
        if self.given is None:
            return ScoreInputs(at=datetime(2026, 10, 3, tzinfo=UTC), observed=())
        return self.given

    def unscored(self) -> list[uuid.UUID]:
        assert self.inside()
        return [scan_id for scan_id in self.backlog if scan_id not in self.recorded]

    def record(self, scan_id: uuid.UUID, scores: ScanScores, *, version: str, at: datetime) -> bool:
        assert self.inside()
        if scan_id in self.recorded:
            return False
        self.recorded[scan_id] = (scores, version, at)
        return True


class RecordingAlerts:
    """An alert store in memory: one alert per (scan, rule), one notification per alert; used
    only inside a unit of work when given one. Scans have no scores unless a context is given."""

    def __init__(
        self, context: ScanAlertContext | None = None, inside: Callable[[], bool] = lambda: True
    ) -> None:
        self.given, self.inside = context, inside
        self.fired: dict[tuple[uuid.UUID, AlertRule], uuid.UUID] = {}
        self.told: dict[uuid.UUID, tuple[str, str]] = {}

    def context(self, scan_id: uuid.UUID) -> ScanAlertContext | None:
        assert self.inside()
        return self.given

    def fire(self, scan_id: uuid.UUID, rule: AlertRule, *, at: datetime) -> uuid.UUID | None:
        assert self.inside()
        if (scan_id, rule) in self.fired:
            return None
        self.fired[scan_id, rule] = uuid.uuid4()
        return self.fired[scan_id, rule]

    def notify(self, alert_id: uuid.UUID, *, title: str, body: str, at: datetime) -> bool:
        assert self.inside()
        if alert_id in self.told:
            return False
        self.told[alert_id] = (title, body)
        return True


class RecordingOutbox:
    """Keeps the alert emails queued, once per alert; only inside a unit of work."""

    def __init__(self, inside: Callable[[], bool] = lambda: True) -> None:
        self.inside = inside
        self.alert_emails: dict[uuid.UUID, dict[str, str]] = {}
        self.otp_emails: dict[uuid.UUID, bytes] = {}

    def add_otp_email(
        self, otp_code_id: uuid.UUID, *, sealed: bytes, minutes: int, at: datetime
    ) -> bool:
        assert self.inside()
        if otp_code_id in self.otp_emails:
            return False
        self.otp_emails[otp_code_id] = sealed
        return True

    def add_alert_email(
        self, alert_id: uuid.UUID, *, data: Mapping[str, str], at: datetime
    ) -> bool:
        assert self.inside()
        if alert_id in self.alert_emails:
            return False
        self.alert_emails[alert_id] = dict(data)
        return True


class RecordingJobs:
    def __init__(self) -> None:
        self.scans: list[uuid.UUID] = []
        self.outbox_nudges = 0

    def run_scan(self, scan_id: uuid.UUID) -> None:
        self.scans.append(scan_id)

    def dispatch_outbox(self) -> None:
        self.outbox_nudges += 1


class FakeUnitOfWork:
    """Shares its stores across units of work; jobs reach `sent` only on a clean exit, and
    `open` says whether a unit of work is in progress (no external call may happen then)."""

    def __init__(self, scans: InMemoryScans, schedules: StaticSchedules) -> None:
        self.scans, self.schedules = scans, schedules
        self.mentions, self.observations = RecordingMentions(), RecordingObservations()
        self.enrichments = RecordingEnrichments()
        self.targets = StaticTargets()
        self.scores = RecordingScores(lambda: self.open)
        self.alerts = RecordingAlerts(inside=lambda: self.open)
        self.outbox = RecordingOutbox(lambda: self.open)
        self.sent = RecordingJobs()
        self.jobs = RecordingJobs()
        self.open = False

    def __call__(self) -> Self:
        return self

    def __enter__(self) -> Self:
        self.jobs, self.open = RecordingJobs(), True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.open = False
        if exc_type is None:
            self.sent.scans.extend(self.jobs.scans)
            self.sent.outbox_nudges += self.jobs.outbox_nudges


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


def ask(query: str) -> SearchRequest:
    return SearchRequest(SerpEngine.GOOGLE, {"q": query})


class ScriptedSearch:
    """Answers each query with its script entry (a payload, or an error to raise), and keeps who
    each search was billed to and the log context it ran in."""

    def __init__(self, script: Mapping[str, object] | None = None) -> None:
        self.script, self.asked = dict(script or {}), list[str]()
        self.billed: list[tuple[uuid.UUID, uuid.UUID | None]] = []
        self.context: list[dict[str, Any]] = []

    def search(
        self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
    ) -> SearchResult:
        query = str(request.params["q"])
        self.asked.append(query)
        self.billed.append((user_id, scan_id))
        self.context.append(structlog.contextvars.get_contextvars())
        answer = self.script.get(query, {})
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, Mapping)
        return SearchResult(uuid.uuid4(), answer, ServedFrom.LIVE)


STUB_SOURCE = MappingProxyType(
    {
        Surface.NEWS: MentionSource.NEWS,
        Surface.AUTOCOMPLETE: MentionSource.AUTOCOMPLETE,
        Surface.SEARCH_PAGE: MentionSource.PEOPLE_ALSO_ASK,
        Surface.AI_OVERVIEW: MentionSource.AI_OVERVIEW,
    }
)


class StubCollector:
    """A collector for `surface` asking for `queries`, whose leads may `also` show other
    surfaces, and which `enables` more surfaces still (for follow-ups its leads don't name). An
    answer can name the `next` page, an AI `overview` to ask for, what it `shows` (surfaces it
    has mentions for, by default its lead's) and what it has `not_shown`."""

    def __init__(
        self,
        surface: Surface,
        *queries: str,
        pages: int = 1,
        also: frozenset[Surface] = frozenset(),
        enables: frozenset[Surface] = frozenset(),
    ) -> None:
        self.surface, self.queries, self.pages, self.also = surface, queries, pages, also
        self.enables = enables

    def enabled(self, target: Target) -> frozenset[Surface]:
        return frozenset({self.surface, *self.also, *self.enables})

    def leads(self, target: Target) -> list[Lead]:
        return [Lead(self.surface, ask(q), self.also, pages=self.pages) for q in self.queries]

    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        shows = payload.get("shows", [lead.surface])
        query = str(lead.request.params["q"])
        mentions = tuple(
            ParsedMention(STUB_SOURCE[s], text_key(f"{query} {s}"), f"{query} {s}") for s in shows
        )
        follow_ups = []
        if "next" in payload:
            following = ask(payload["next"])  # only a first page shows `also`
            follow_ups.append(
                replace(lead, request=following, page=lead.page + 1, also=frozenset())
            )
        if "overview" in payload:
            follow_ups.append(Lead(Surface.AI_OVERVIEW, ask(payload["overview"])))
        not_shown = frozenset(payload.get("not_shown", ()))
        return Reading(mentions=mentions, not_shown=not_shown, follow_ups=tuple(follow_ups))


class Untouchable(Mapping[str, Any]):
    """A payload that fails if anything reads it."""

    def __getitem__(self, key: str) -> Any:
        raise AssertionError("the payload was read")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("the payload was read")

    def __len__(self) -> int:
        raise AssertionError("the payload was read")


class FixedProfiles:
    """Every user's tasks run on the same settings, and the users asked about are kept."""

    def __init__(self, settings: TaskSettings) -> None:
        self.fixed = settings
        self.asked: list[tuple[uuid.UUID, LlmTask]] = []

    def settings(self, user_id: uuid.UUID, task: LlmTask) -> TaskSettings:
        self.asked.append((user_id, task))
        return self.fixed

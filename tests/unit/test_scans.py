"""Running one scan with fakes: the claim, the skips, collection, labelling and the finish."""

import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from serpsense.domain.enums import (
    LlmTask,
    MentionSource,
    ScanStatus,
    ScanTrigger,
    SerpErrorCode,
    Surface,
)
from serpsense.domain.estimator import BrandFacts, estimate
from serpsense.domain.llm_capabilities import HAIKU, OPUS, Effort, TaskSettings
from serpsense.domain.scan_state import Transition, TransitionReason
from serpsense.domain.settings.search import resolve
from serpsense.ports.enrichment_store import PendingText
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, PromptUnavailable
from serpsense.ports.scan_store import NewScan
from serpsense.ports.scan_targets import NamedBrand, ScanTarget, StoreApp
from serpsense.ports.search_provider import SearchFailed, SearchRequest
from serpsense.services.collection import CollectorRunner
from serpsense.services.labelling import Labeller
from serpsense.services.llm_gateway import LlmGateway
from serpsense.services.scans import ScanLimits, ScanPorts, ScanService
from serpsense.services.search import SearchResult
from tests.fakes import (
    FakeUnitOfWork,
    FixedClock,
    FixedProfiles,
    InMemoryScans,
    MemoryLedger,
    RecordingEnrichments,
    ScriptedLlm,
    ScriptedSearch,
    StaticSchedules,
    StaticTargets,
    StubCollector,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
OWNER, BRAND = uuid.uuid4(), uuid.uuid4()
RIVAL = NamedBrand(uuid.uuid4(), "Uber")
CABS = StoreApp(uuid.uuid4(), "com.olacabs.customer")
SNAPSHOT = resolve({}).model_dump(mode="json")
MOST = estimate(resolve({}), BrandFacts(apps=1, locations=0))  # the Standard preset, one app
S, R = ScanStatus, TransitionReason
FAILED = SearchFailed(SerpErrorCode.HTTP_5XX, http_status=503, latency_ms=40)
SETTINGS = TaskSettings(OPUS, Effort.LOW, 8000)


@dataclass
class Usage:
    used: int = 0
    budget: int | None = None

    def monthly_budget(self, user_id: uuid.UUID, *, at: datetime) -> int | None:
        return self.budget

    def live_calls(
        self, *, since: datetime, user_id: uuid.UUID | None = None, scan_id: uuid.UUID | None = None
    ) -> int:
        assert (since, user_id) == (datetime(2026, 10, 1, tzinfo=UTC), OWNER)
        return self.used


@dataclass
class Scan:
    service: ScanService
    uow: FakeUnitOfWork
    scan_id: uuid.UUID
    llm: ScriptedLlm
    ledger: MemoryLedger
    profiles: FixedProfiles


def scan(searcher: ScriptedSearch, *collectors: StubCollector, **options: Any) -> Scan:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    new = NewScan(BRAND, ScanTrigger.MANUAL, SNAPSHOT, MOST, NOW, requested_by=OWNER)
    scan_id = uow.scans.create(new)
    assert scan_id is not None
    brand = NamedBrand(BRAND, "Ola")
    target = ScanTarget(
        scan_id, brand, OWNER, SNAPSHOT, ("Ola Cabs",), (RIVAL,), (CABS,), False, False
    )
    uow.targets = StaticTargets(replace(target, **options.pop("target", {})))
    pending = PendingText(uuid.uuid4(), 1, MentionSource.NEWS, "en", "Ola driver late")
    uow.enrichments = RecordingEnrichments([pending])
    clock = FixedClock(NOW)
    runner = CollectorRunner(collectors or [StubCollector(Surface.NEWS, "news")], searcher, clock)

    answering = options.pop("answer", labels)

    def answer(request: LlmRequest) -> str | LlmCallFailed:
        assert not uow.open  # no model call inside a unit of work
        return answering(request)  # type: ignore[no-any-return]

    llm, ledger = ScriptedLlm(answer), MemoryLedger(spent=options.pop("llm_spent", 0))
    gateway = LlmGateway(llm, ledger, clock, monthly_budget_micros=lambda u: 30_000_000)
    profiles = FixedProfiles(options.pop("settings", SETTINGS))
    usage = Usage(**options)
    ports = ScanPorts(
        lambda: uow, runner, Labeller(lambda: uow, gateway, clock), usage, profiles, clock
    )
    service = ScanService(ports, ScanLimits(timedelta(minutes=10), monthly_searches=1500))
    return Scan(service, uow, scan_id, llm, ledger, profiles)


def labels(request: LlmRequest) -> str | LlmCallFailed:
    records = request.variables["mentions"]
    assert isinstance(records, list)
    label = {"is_about_brand": True, "sentiment": -1, "topic": "reliability"}
    rest = {"is_complaint": True, "severity": 30, "reason": "Late."}
    return json.dumps({"labels": [{"id": r["id"], **label, **rest} for r in records]})


class Outside(ScriptedSearch):
    """Fails a search made while a unit of work is open."""

    uow: FakeUnitOfWork

    def search(
        self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
    ) -> SearchResult:
        assert not self.uow.open
        return super().search(request, user_id=user_id, scan_id=scan_id)


def moves(run: Scan) -> list[tuple[ScanStatus | None, ScanStatus, TransitionReason]]:
    return [(t.from_status, t.to_status, t.reason) for _, t, _ in run.uow.scans.transitions]


def test_a_queued_scan_is_claimed_collected_kept_labelled_and_succeeds() -> None:
    searcher = Outside()
    run = scan(searcher)
    searcher.uow = run.uow
    assert run.service.run(run.scan_id) is S.SUCCEEDED
    assert moves(run) == [(S.QUEUED, S.RUNNING, R.CLAIMED), (S.RUNNING, S.SUCCEEDED, R.COMPLETED)]
    assert searcher.billed == [(OWNER, run.scan_id)]  # billed to the brand's owner
    assert set(run.uow.scans.surfaces[run.scan_id]) == set(Surface)
    assert [m.text for s in run.uow.mentions.sightings for m in s.mentions] == ["news news"]
    assert len(run.uow.enrichments.labels) == 1 and run.uow.enrichments.asked_for == [BRAND]
    assert run.profiles.asked == [(OWNER, LlmTask.LABEL_MENTIONS)]
    assert [call.user_id for call in run.ledger.calls] == [OWNER]  # the model call too
    assert run.llm.requests[0].variables["aliases"] == "Ola Cabs"


def test_a_scan_another_worker_has_is_left_alone() -> None:
    searcher = ScriptedSearch()
    run = scan(searcher)
    run.service.run(run.scan_id)
    assert run.service.run(run.scan_id) is None  # a redelivered message
    assert len(searcher.asked) == 1 and len(moves(run)) == 2


@pytest.mark.parametrize(
    ("gone", "reason"),
    [({"brand_archived": True}, R.BRAND_ARCHIVED), ({"owner_deleted": True}, R.ACCOUNT_DELETED)],
)
def test_a_scan_of_a_brand_that_is_gone_is_skipped_before_any_search(
    gone: dict[str, bool], reason: TransitionReason
) -> None:
    searcher = ScriptedSearch()
    run = scan(searcher, target=gone)
    assert run.service.run(run.scan_id) is S.SKIPPED
    assert moves(run)[-1] == (S.RUNNING, S.SKIPPED, reason) and searcher.asked == []


def test_a_brand_archived_while_collecting_gets_no_model_call() -> None:
    run: Scan

    class Archiving(ScriptedSearch):
        def search(
            self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
        ) -> SearchResult:
            targets = run.uow.targets.targets
            targets[run.scan_id] = replace(targets[run.scan_id], brand_archived=True)
            return super().search(request, user_id=user_id, scan_id=scan_id)

    run = scan(Archiving())
    assert run.service.run(run.scan_id) is S.SKIPPED
    assert moves(run)[-1] == (S.RUNNING, S.SKIPPED, R.BRAND_ARCHIVED)
    assert run.uow.mentions.sightings and run.llm.requests == []  # what was seen is kept


def test_a_brand_archived_while_labelling_finishes_skipped() -> None:
    run: Scan

    def archiving(request: LlmRequest) -> str | LlmCallFailed:
        targets = run.uow.targets.targets
        targets[run.scan_id] = replace(targets[run.scan_id], brand_archived=True)
        return labels(request)

    run = scan(ScriptedSearch(), answer=archiving)
    assert run.service.run(run.scan_id) is S.SKIPPED
    assert moves(run)[-1] == (S.RUNNING, S.SKIPPED, R.BRAND_ARCHIVED)


def test_a_scan_the_owners_searches_cant_cover_is_skipped() -> None:
    searcher = ScriptedSearch()
    run = scan(searcher, used=1500 - MOST + 1)  # its brand's app counts too
    assert run.service.run(run.scan_id) is S.SKIPPED
    assert moves(run)[-1] == (S.RUNNING, S.SKIPPED, R.BUDGET_EXHAUSTED) and searcher.asked == []
    nothing = scan(ScriptedSearch(), budget=0)  # a budget row of 0 is a budget, not "none set"
    assert nothing.service.run(nothing.scan_id) is S.SKIPPED
    for enough in (scan(ScriptedSearch(), used=1500 - MOST), scan(ScriptedSearch(), budget=MOST)):
        assert enough.service.run(enough.scan_id) is S.SUCCEEDED


def test_a_scan_is_never_skipped_for_more_than_it_may_make() -> None:
    capped = {"settings_snapshot": resolve({"max_searches": 2}).model_dump(mode="json")}
    run = scan(ScriptedSearch(), target=capped, used=1498)  # 2 left: what the scan may make
    assert run.service.run(run.scan_id) is S.SUCCEEDED


def test_a_scan_finishes_by_how_its_surfaces_and_labelling_went() -> None:
    some = scan(ScriptedSearch({"down": FAILED}), StubCollector(Surface.NEWS, "news", "down"))
    assert some.service.run(some.scan_id) is S.PARTIAL
    assert moves(some)[-1] == (S.RUNNING, S.PARTIAL, R.SURFACES_FAILED)
    every = scan(ScriptedSearch({"down": FAILED}), StubCollector(Surface.NEWS, "down"))
    assert every.service.run(every.scan_id) is S.FAILED
    assert moves(every)[-1] == (S.RUNNING, S.FAILED, R.ALL_SURFACES_FAILED)
    timeout = LlmCallFailed("llm.timeout", retryable=True, latency_ms=10)
    unlabelled = scan(ScriptedSearch(), answer=lambda request: timeout)
    assert unlabelled.service.run(unlabelled.scan_id) is S.PARTIAL
    assert moves(unlabelled)[-1] == (S.RUNNING, S.PARTIAL, R.ENRICHMENT_FAILED)
    spent = scan(ScriptedSearch(), llm_spent=30_000_000)  # the month's model budget is used up
    assert spent.service.run(spent.scan_id) is S.PARTIAL and spent.llm.requests == []


@pytest.mark.parametrize("misconfigured", ["settings", "prompt"])
def test_labelling_that_cant_run_leaves_the_scan_partial(misconfigured: str) -> None:
    def missing(request: LlmRequest) -> str | LlmCallFailed:
        raise PromptUnavailable("label_mentions/v1")

    bad = {"settings": TaskSettings(HAIKU, Effort.MAX, 1024)} if misconfigured == "settings" else {}
    run = scan(
        ScriptedSearch(), **bad, **({"answer": missing} if misconfigured == "prompt" else {})
    )
    with capture_logs() as logs:
        assert run.service.run(run.scan_id) is S.PARTIAL
    assert moves(run)[-1] == (S.RUNNING, S.PARTIAL, R.ENRICHMENT_FAILED)
    assert "labelling.misconfigured" in [entry["event"] for entry in logs]


def test_a_stage_that_raises_fails_the_scan_and_the_error_propagates() -> None:
    run = scan(ScriptedSearch({"news": RuntimeError("ledger down")}))
    with pytest.raises(RuntimeError, match="ledger down"):
        run.service.run(run.scan_id)
    assert moves(run)[-1] == (S.RUNNING, S.FAILED, R.STAGE_FAILED)
    corrupt = {"settings_snapshot": {**SNAPSHOT, "unknown": 1}}
    searcher = ScriptedSearch()
    bad = scan(searcher, target=corrupt)
    with pytest.raises(ValueError, match="unknown"):
        bad.service.run(bad.scan_id)
    assert moves(bad)[-1] == (S.RUNNING, S.FAILED, R.STAGE_FAILED) and searcher.asked == []


def test_a_scan_whose_row_went_missing_fails() -> None:
    run = scan(ScriptedSearch())
    run.uow.targets.targets.clear()
    with pytest.raises(LookupError, match="row is gone"):
        run.service.run(run.scan_id)
    assert moves(run)[-1] == (S.RUNNING, S.FAILED, R.STAGE_FAILED)


def test_a_failure_to_finish_doesnt_hide_the_stages_error() -> None:
    run = scan(ScriptedSearch({"news": RuntimeError("ledger down")}))
    real_move = run.uow.scans.move

    def move(scan_id: uuid.UUID, transition: Transition, *, at: datetime) -> bool:
        if transition.reason is R.STAGE_FAILED:
            raise ConnectionError("database down too")
        return real_move(scan_id, transition, at=at)

    run.uow.scans.move = move  # type: ignore[method-assign]
    with capture_logs() as logs, pytest.raises(RuntimeError, match="ledger down"):
        run.service.run(run.scan_id)
    events = [entry["event"] for entry in logs]
    assert events.index("scan.stage_failed") < events.index("scan.finish_failed")


def test_a_scan_the_sweep_timed_out_meanwhile_keeps_that_ending() -> None:
    run: Scan
    timed_out = Transition(S.RUNNING, S.FAILED, R.TIMED_OUT)

    class Slow(ScriptedSearch):
        def search(
            self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
        ) -> SearchResult:
            assert run.uow.scans.move(run.scan_id, timed_out, at=NOW)  # the sweep, meanwhile
            return super().search(request, user_id=user_id, scan_id=scan_id)

    run = scan(Slow())
    assert run.service.run(run.scan_id) is None
    assert moves(run)[-1] == (S.RUNNING, S.FAILED, R.TIMED_OUT)

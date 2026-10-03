"""Grouping a brand's unfavourable mentions into narratives, with a scripted model and in-memory
stores."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from serpsense.domain.enums import LlmTask, MentionSource, Topic
from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings
from serpsense.ports.clock import Clock
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.ports.narrative_store import OpenNarrative, UngroupedMention
from serpsense.services.grouping import BATCH, Grouped, Grouper
from serpsense.services.labelling import BrandContext
from serpsense.services.llm_gateway import LlmGateway
from tests.fakes import (
    Answerer,
    FakeUnitOfWork,
    FixedClock,
    InMemoryScans,
    MemoryLedger,
    RecordingNarratives,
    ScriptedLlm,
    StaticSchedules,
    TickingClock,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
OLA = BrandContext(uuid.uuid4(), uuid.uuid4(), "Ola", ("Ola Cabs",))
SETTINGS = TaskSettings(OPUS, Effort.MEDIUM, 8000)
CASH = OpenNarrative(uuid.uuid4(), "Drivers demand cash above the fare", "Riders report it.", 4)
TIMEOUT = LlmCallFailed("llm.timeout", retryable=True, latency_ms=10)
BAD_REQUEST = LlmCallFailed("llm.http_4xx", retryable=False, latency_ms=10)


def waiting(count: int, labelled_at: datetime = NOW - timedelta(hours=1)) -> list[UngroupedMention]:
    return [
        UngroupedMention(
            mention_id=uuid.uuid4(),
            source=MentionSource.PLAY_REVIEW,
            language_code="en",
            text=f"AC extra {n}",
            topic=Topic.PRICING,
            severity=30,
            labelled_at=labelled_at,
        )
        for n in range(count)
    ]


def to_cash_and_a_new_story(request: LlmRequest) -> str:
    """m1 joins n1, m2 and m3 start a story, the rest are one-offs."""
    records = request.variables["mentions"]
    assert isinstance(records, list)
    targets = {"m1": "n1", "m2": "new1", "m3": "new1"}
    placed = [{"id": r["id"], "narrative": targets.get(r["id"])} for r in records]
    story = {"id": "new1", "label": "Refunds not credited", "summary": "Riders wait for refunds."}
    return json.dumps({"new_narratives": [story], "placements": placed})


class Run:
    """A grouper over in-memory stores; the model may only be asked outside a unit of work."""

    def __init__(self, pending: list[UngroupedMention], answer: Answerer) -> None:
        self.uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
        self.store = self.uow.narratives = RecordingNarratives(pending, [CASH])

        def outside(request: LlmRequest) -> str | LlmCallFailed:
            assert not self.uow.open
            return answer(request)

        self.llm, self.ledger = ScriptedLlm(outside), MemoryLedger()

    def group(
        self,
        clock: Clock | None = None,
        *,
        budget: int = 30_000_000,
        deadline: datetime | None = None,
    ) -> Grouped:
        clock = clock or FixedClock(NOW)
        gateway = LlmGateway(self.llm, self.ledger, clock, monthly_budget_micros=lambda u: budget)
        return Grouper(lambda: self.uow, gateway, clock).group(OLA, SETTINGS, deadline=deadline)


def test_waiting_mentions_are_placed_in_batches_offered_the_open_narratives() -> None:
    pending = waiting(BATCH + 3)
    run = Run(pending, to_cash_and_a_new_story)
    assert run.group() == Grouped(placed=6, failed_batches=0)
    assert [len(r.variables["mentions"]) for r in run.llm.requests] == [BATCH, 3]
    assert run.store.prompts == [{LlmTask.LABEL_MENTIONS: "label_mentions/v1"}]
    first = run.llm.requests[0].variables
    assert (first["brand"], first["aliases"]) == ("Ola", "Ola Cabs")
    cash = {"id": "n1", "mentions": "4", "label": CASH.label, "text": "Riders report it."}
    assert first["narratives"] == [cash]
    second = run.llm.requests[1].variables["narratives"]
    assert isinstance(second, list) and second[0]["label"] == "Refunds not credited"  # read again
    (started, version, call_id), *_ = run.store.started
    assert (started.brand_id, version, call_id) == (
        OLA.brand_id,
        "group_narratives/v1",
        uuid.UUID(int=1),
    )
    placed = [(a.mention_id, a.narrative_id) for a, _, _ in run.store.placed[:3]]
    new = started.narrative_id
    assert placed == [
        (pending[0].mention_id, CASH.narrative_id),
        (pending[1].mention_id, new),
        (pending[2].mention_id, new),
    ]


def test_one_offs_keep_waiting_but_are_sent_again_only_with_a_newer_label() -> None:
    alone = {
        "new_narratives": [],
        "placements": [{"id": f"m{n}", "narrative": None} for n in (1, 2)],
    }
    run = Run(waiting(2), lambda request: json.dumps(alone))
    assert (
        run.group() == Grouped(0, 0) and len(run.llm.requests) == 1 and run.store.considered == NOW
    )
    assert run.group(FixedClock(NOW + timedelta(hours=12))) == Grouped(0, 0)
    assert len(run.llm.requests) == 1  # nothing labelled since: no call
    run.store.waiting += waiting(1, labelled_at=NOW + timedelta(hours=11))
    run.group(FixedClock(NOW + timedelta(hours=12)))
    assert len(run.llm.requests[-1].variables["mentions"]) == 3  # the one-offs come along


def test_answers_it_cant_use_are_logged_by_count() -> None:
    def answer(request: LlmRequest) -> str:
        placed = [{"id": "m1", "narrative": "n9"}, {"id": "m7", "narrative": None}]
        return json.dumps({"new_narratives": [], "placements": placed})

    with capture_logs() as logs:
        assert Run(waiting(2), answer).group() == Grouped(placed=0, failed_batches=0)
    (dropped,) = [entry for entry in logs if entry["event"] == "grouping.placements_dropped"]
    assert (dropped["dropped"], dropped["ignored"]) == (2, 1)


def test_no_batch_starts_past_the_deadline() -> None:
    clock = TickingClock(*(NOW + timedelta(minutes=m) for m in range(0, 30)))
    run = Run(waiting(3 * BATCH), to_cash_and_a_new_story)
    result = run.group(clock, deadline=NOW + timedelta(minutes=4))
    assert result.out_of_time and not result.complete and len(run.llm.requests) == 1
    assert Grouped(3, 0).complete and not Grouped(3, 1).complete


def failing(*answers: LlmCallFailed | None) -> Answerer:
    queue = iter(answers)

    def answer(request: LlmRequest) -> str | LlmCallFailed:
        return next(queue, None) or to_cash_and_a_new_story(request)

    return answer


def test_a_failed_batch_is_skipped_and_its_mentions_keep_waiting() -> None:
    run = Run(waiting(BATCH + 1), failing(TIMEOUT))
    assert run.group() == Grouped(placed=1, failed_batches=1) and len(run.store.placed) == 1


def test_two_failed_batches_in_a_row_or_one_no_retry_fixes_stop_the_run() -> None:
    run = Run(waiting(3 * BATCH), failing(TIMEOUT, TIMEOUT))
    with capture_logs() as logs:
        assert run.group() == Grouped(placed=0, failed_batches=2) and len(run.llm.requests) == 2
    assert "grouping.stopped" in [entry["event"] for entry in logs]
    run = Run(waiting(2 * BATCH), failing(BAD_REQUEST))
    assert run.group() == Grouped(placed=0, failed_batches=1) and len(run.llm.requests) == 1


def test_invalid_output_fails_its_batch_and_a_spent_budget_or_nothing_waiting_asks_nothing() -> (
    None
):
    nul = {"id": "new1", "label": "Fares\u0000", "summary": "Riders wait."}
    invalid = Run(
        waiting(1), lambda request: json.dumps({"new_narratives": [nul], "placements": []})
    )
    assert invalid.group() == Grouped(placed=0, failed_batches=1)
    spent = Run(waiting(1), to_cash_and_a_new_story)
    assert spent.group(budget=0) == Grouped(0, 0, budget_exhausted=True)
    assert spent.llm.requests == []
    idle = Run([], to_cash_and_a_new_story)
    assert idle.group() == Grouped(placed=0, failed_batches=0) and idle.llm.requests == []

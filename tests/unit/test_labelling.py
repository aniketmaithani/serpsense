"""Labelling a brand's recent mentions, with a scripted model and in-memory stores."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from serpsense.domain.enums import MentionSource, Topic
from serpsense.domain.llm_capabilities import HAIKU, OPUS, Effort, TaskSettings, UnsupportedSetting
from serpsense.ports.enrichment_store import MentionLabel, PendingText
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.services.labelling import BATCH, BrandContext, Labelled, Labeller
from serpsense.services.llm_gateway import LlmGateway
from tests.fakes import (
    FakeUnitOfWork,
    FixedClock,
    InMemoryScans,
    MemoryLedger,
    RecordingEnrichments,
    ScriptedLlm,
    StaticSchedules,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
OLA = BrandContext(uuid.uuid4(), uuid.uuid4(), "Ola", ("Ola Cabs",), "Ola Electric")
SETTINGS = TaskSettings(OPUS, Effort.LOW, 8000)


def texts(count: int, source: MentionSource = MentionSource.PLAY_REVIEW) -> list[PendingText]:
    return [PendingText(uuid.uuid4(), 1, source, "en", f"late driver {n}") for n in range(count)]


def complaint(mention_id: str, **overrides: object) -> dict[str, object]:
    values = {
        "id": mention_id,
        "is_about_brand": True,
        "sentiment": -1,
        "severity": 30,
        "topic": "reliability",
        "is_complaint": True,
        "reason": "A late driver.",
    }
    return {**values, **overrides}


def labels_for_each(request: LlmRequest, **overrides: object) -> str:
    records = request.variables["mentions"]
    assert isinstance(records, list)
    return json.dumps({"labels": [complaint(record["id"], **overrides) for record in records]})


def run(
    pending: list[PendingText], answer: object, spent: int = 0
) -> tuple[Labelled, FakeUnitOfWork, ScriptedLlm]:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    uow.enrichments = RecordingEnrichments(pending)
    client = ScriptedLlm(answer)  # type: ignore[arg-type]  # each test's answer function
    gateway = LlmGateway(
        client, MemoryLedger(spent), FixedClock(NOW), monthly_budget_micros=lambda u: 30_000_000
    )
    result = Labeller(lambda: uow, gateway, FixedClock(NOW)).label(OLA, SETTINGS, scan_id=None)
    return result, uow, client


def test_pending_texts_are_labelled_in_batches_with_the_brand_as_data() -> None:
    pending = texts(BATCH + 3)
    result, uow, client = run(pending + texts(2, MentionSource.AUTOCOMPLETE), labels_for_each)
    assert result == Labelled(labelled=BATCH + 3, failed_batches=0)
    sizes = [len(r.variables["mentions"]) for r in client.requests]
    assert sizes == [BATCH, 3]  # autocomplete is another task's
    first = client.requests[0].variables
    brand = ("Ola", "Ola Cabs", "Ola Electric")
    assert (first["brand"], first["aliases"], first["not_the_brand"]) == brand
    record = {"id": "m1", "source": "play_review", "text": "late driver 0", "language": "en"}
    assert isinstance(first["mentions"], list) and first["mentions"][0] == record
    label = MentionLabel(
        pending[0].mention_id, 1, -1, 30, Topic.RELIABILITY, True, True, "A late driver."
    )
    assert uow.enrichments.labels[0] == (label, "label_mentions/v1", uuid.UUID(int=1))


def test_a_label_about_the_brand_keeps_what_the_model_said() -> None:
    praise = {"sentiment": 1, "severity": 0, "topic": "pricing", "is_complaint": False}
    _, uow, _ = run(texts(1), lambda request: labels_for_each(request, **praise))
    ((label, _, _),) = uow.enrichments.labels
    got = (label.sentiment, label.severity, label.topic, label.is_complaint, label.is_about_brand)
    assert got == (1, 0, Topic.PRICING, False, True)


def test_a_text_not_about_the_brand_gets_the_fixed_values() -> None:
    def answer(request: LlmRequest) -> str:
        return json.dumps(
            {"labels": [complaint("m1", is_about_brand=False, topic="safety", severity=90)]}
        )

    _, uow, _ = run(texts(1), answer)
    ((label, _, _),) = uow.enrichments.labels
    got = (label.is_about_brand, label.sentiment, label.severity, label.topic, label.is_complaint)
    assert got == (False, 0, 0, Topic.OTHER, False)


def test_unknown_and_repeated_ids_are_ignored_and_missing_ones_stay_pending() -> None:
    def answer(request: LlmRequest) -> str:
        return json.dumps(
            {"labels": [complaint("m1"), complaint("m1", severity=99), complaint("m9")]}
        )

    pending = texts(2)
    result, uow, _ = run(pending, answer)
    assert result.labelled == 1 and uow.enrichments.labels[0][0].severity == 30  # the first answer
    labelled = [label.mention_id for label, _, _ in uow.enrichments.labels]
    assert labelled == [pending[0].mention_id]  # m2 had no label, so it stays pending


def test_a_failed_batch_is_skipped_and_the_rest_go_on() -> None:
    calls = iter([LlmCallFailed("llm.timeout", retryable=True, latency_ms=10), None])

    def answer(request: LlmRequest) -> object:
        return next(calls) or labels_for_each(request)

    result, _, _ = run(texts(BATCH + 1), answer)
    assert result == Labelled(labelled=1, failed_batches=1)


def test_a_spent_budget_stops_the_run() -> None:
    result, _, client = run(texts(3), labels_for_each, spent=30_000_000)
    assert result == Labelled(labelled=0, failed_batches=0, budget_exhausted=True)
    assert client.requests == []


def failing(*codes: LlmCallFailed | None) -> object:
    answers = iter(codes)

    def answer(request: LlmRequest) -> object:
        return next(answers, None) or labels_for_each(request)

    return answer


TIMEOUT = LlmCallFailed("llm.timeout", retryable=True, latency_ms=10)


def test_two_failed_batches_in_a_row_stop_the_run() -> None:
    with capture_logs() as logs:
        result, _, client = run(texts(3 * BATCH), failing(TIMEOUT, TIMEOUT))
    assert result == Labelled(labelled=0, failed_batches=2) and len(client.requests) == 2
    assert "labelling.stopped" in [entry["event"] for entry in logs]


def test_failures_with_a_success_between_them_dont_stop_the_run() -> None:
    result, _, client = run(texts(4 * BATCH), failing(TIMEOUT, None, TIMEOUT))
    assert result == Labelled(labelled=2 * BATCH, failed_batches=2) and len(client.requests) == 4


def test_a_failure_no_retry_can_fix_stops_the_run_at_once() -> None:
    bad_request = LlmCallFailed("llm.http_4xx", retryable=False, latency_ms=10)
    result, _, client = run(texts(2 * BATCH), failing(bad_request))
    assert result == Labelled(labelled=0, failed_batches=1) and len(client.requests) == 1


def test_a_refused_batch_counts_as_a_failure() -> None:
    def answer(request: LlmRequest) -> object:
        return "not json at all"  # the gateway rejects it as invalid output

    result, _, _ = run(texts(1), answer)
    assert result == Labelled(labelled=0, failed_batches=1)


def test_a_blank_reason_rejects_only_its_batch() -> None:
    blank = iter([True])

    def answer(request: LlmRequest) -> object:
        return (
            labels_for_each(request, reason=" \n")
            if next(blank, False)
            else labels_for_each(request)
        )

    result, _, _ = run(texts(BATCH + 1), answer)
    assert result == Labelled(labelled=1, failed_batches=1)


def labeller(uow: FakeUnitOfWork, ledger: MemoryLedger, budget: int = 30_000_000) -> Labeller:
    client = ScriptedLlm(labels_for_each)
    gateway = LlmGateway(client, ledger, FixedClock(NOW), monthly_budget_micros=lambda u: budget)
    return Labeller(lambda: uow, gateway, FixedClock(NOW))


def test_a_budget_spent_mid_run_stops_it_and_a_second_run_labels_nothing_twice() -> None:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    uow.enrichments = RecordingEnrichments(texts(BATCH + 1))
    ledger = MemoryLedger(spent_after=30_000_000)  # the first call uses up the month
    assert labeller(uow, ledger).label(OLA, SETTINGS) == Labelled(BATCH, 0, budget_exhausted=True)
    ledger.spent, ledger.spent_after = 0, None  # a new month
    assert labeller(uow, ledger).label(OLA, SETTINGS) == Labelled(1, 0)  # only what was left
    assert labeller(uow, ledger).label(OLA, SETTINGS) == Labelled(0, 0)


def test_settings_the_model_rejects_are_raised() -> None:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    uow.enrichments = RecordingEnrichments(texts(1))
    with pytest.raises(UnsupportedSetting):
        labeller(uow, MemoryLedger()).label(OLA, TaskSettings(HAIKU, Effort.MAX, 1024))

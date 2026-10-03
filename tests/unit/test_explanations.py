"""Explaining an alert: the model is told the facts, its words are stored once, and nothing it
does (or fails to do) touches the alert."""

import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType, TracebackType
from typing import Self

import pytest

from serpsense.domain.enums import AlertRule, CrisisComponent, CrisisLevel, LlmTask, MentionSource
from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings
from serpsense.ports.explanations import AlertBrief, BriefMention
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.services.explanations import PROMPT_VERSION, Explained, Explainer
from serpsense.services.llm_gateway import LlmGateway
from tests.fakes import FixedClock, FixedProfiles, MemoryLedger, RecordingJobs, ScriptedLlm

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
SETTINGS = TaskSettings(OPUS, Effort.MEDIUM, 4000)
BRIEF = AlertBrief(
    alert_id=uuid.uuid4(),
    scan_id=uuid.uuid4(),
    owner_id=uuid.uuid4(),
    brand_name="Ola",
    competitor=False,
    rule=AlertRule.LEVEL_INCREASE,
    level=CrisisLevel.MEDIUM,
    previous_level=CrisisLevel.LOW,
    crisis=46,
    health=58,
    components=MappingProxyType({CrisisComponent.VELOCITY: 70, CrisisComponent.PRESS: 20}),
    story_label=None,
    story_summary=None,
    mentions=(
        BriefMention(
            source=MentionSource.PLAY_REVIEW, text="Driver asked ₹100 extra", sentiment=-1
        ),
        BriefMention(source=MentionSource.NEWS, text="City tightens surge rules", sentiment=None),
    ),
    explained=False,
)
SAID = "Ola's crisis level rose from low to medium, driven by reviews about extra cash."


class Explanations:
    def __init__(self, brief: AlertBrief | None, *, record: bool = True) -> None:
        self.given, self.answer = brief, record
        self.recorded: list[tuple[uuid.UUID, str, str, uuid.UUID, datetime]] = []

    def brief(self, alert_id: uuid.UUID) -> AlertBrief | None:
        return self.given

    def record(
        self, alert_id: uuid.UUID, *, text: str, prompt_version: str, llm_call_id: uuid.UUID,
        at: datetime,
    ) -> bool:  # fmt: skip
        self.recorded.append((alert_id, text, prompt_version, llm_call_id, at))
        return self.answer


class UnitOfWork:
    """Only what the explainer uses; jobs reach `sent` on a clean exit."""

    def __init__(self, explanations: Explanations) -> None:
        self.explanations, self.open = explanations, False
        self.jobs, self.sent = RecordingJobs(), RecordingJobs()

    def __call__(self) -> Self:
        return self

    def __enter__(self) -> Self:
        self.jobs, self.open = RecordingJobs(), True
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:  # fmt: skip
        self.open = False
        if exc_type is None:
            self.sent.outbox_nudges += self.jobs.outbox_nudges


def explainer(
    store: Explanations, answer: Callable[[LlmRequest], str | LlmCallFailed]
) -> tuple[Explainer, UnitOfWork, ScriptedLlm]:
    uow = UnitOfWork(store)

    def checked(request: LlmRequest) -> str | LlmCallFailed:
        assert not uow.open  # no unit of work stays open while the model thinks
        return answer(request)

    llm, ledger = ScriptedLlm(checked), MemoryLedger()
    gateway = LlmGateway(llm, ledger, FixedClock(NOW), monthly_budget_micros=lambda _: 10**9)
    service = Explainer(uow, gateway, FixedProfiles(SETTINGS), FixedClock(NOW))  # type: ignore[arg-type]  # the explainer's slice of a unit of work
    return service, uow, llm


def says(text: str) -> Callable[[LlmRequest], str]:
    return lambda request: json.dumps({"explanation": text})


def test_the_model_is_told_the_facts_and_its_words_are_stored_with_provenance() -> None:
    store = Explanations(BRIEF)
    service, uow, llm = explainer(store, says(SAID))
    assert service.explain(BRIEF.alert_id) is Explained.EXPLAINED
    ((alert_id, text, prompt, call_id, at),) = store.recorded
    assert (alert_id, text, prompt, at) == (BRIEF.alert_id, SAID, PROMPT_VERSION, NOW)
    assert call_id == uuid.UUID(int=1) and uow.sent.outbox_nudges == 1  # its email may go now
    (request,) = llm.requests
    sent = request.variables
    assert (request.task, request.prompt_version) == (LlmTask.EXPLAIN_CRISIS, PROMPT_VERSION)
    assert (sent["brand"], sent["rule"], sent["level"], sent["previous_level"]) == (
        "Ola", "level_increase", "medium", "low",
    )  # fmt: skip
    assert sent["components"] == "velocity 70, spread 0, autocomplete 0, trends 0, press 20"
    assert sent["mentions"] == [
        {"source": "play_review", "sentiment": "-1", "text": "Driver asked ₹100 extra"},
        {"source": "news", "sentiment": "", "text": "City tightens surge rules"},
    ]


@pytest.mark.parametrize(
    ("brief", "expected"),
    [
        (None, Explained.MISSING),
        (BRIEF.__class__(**{**vars(BRIEF), "explained": True}), Explained.ALREADY),
    ],
)
def test_a_missing_or_explained_alert_costs_no_call(
    brief: AlertBrief | None, expected: Explained
) -> None:
    service, _, llm = explainer(Explanations(brief), says(SAID))
    assert service.explain(BRIEF.alert_id) is expected and llm.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        lambda request: LlmCallFailed("llm.timeout", retryable=True, latency_ms=5),
        says("Ola\x00 rose"),  # a control character never reaches an email
        says(" "),
        says("x" * 2001),
        says("Ola's level rose. Call 98765 43210 to fix it."),  # a planted phone number
        says("Ola's level rose; see https://ola-refunds.example for refunds."),
        says(f"Ola's level rose{chr(0x200B)} over airport fares."),  # an invisible character
    ],
)
def test_a_model_that_fails_leaves_the_alert_unexplained(
    answer: Callable[[LlmRequest], str | LlmCallFailed],
) -> None:
    store = Explanations(BRIEF)
    service, uow, _ = explainer(store, answer)
    assert service.explain(BRIEF.alert_id) is Explained.FAILED
    assert store.recorded == [] and uow.sent.outbox_nudges == 0


def test_a_gone_brand_costs_no_call() -> None:
    service, _, llm = explainer(Explanations(replace(BRIEF, gone=True)), says(SAID))
    assert service.explain(BRIEF.alert_id) is Explained.SKIPPED and llm.requests == []


def test_an_explanation_written_meanwhile_is_kept() -> None:
    service, _, _ = explainer(Explanations(BRIEF, record=False), says(SAID))
    assert service.explain(BRIEF.alert_id) is Explained.ALREADY

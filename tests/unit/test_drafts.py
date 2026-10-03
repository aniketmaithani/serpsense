"""Drafting a response: the model is shown the story under short ids, must cite what it answers,
and a draft it can't stand behind is never stored."""

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest

from serpsense.domain.enums import DraftKind, DraftPreset, LlmTask, MentionSource
from serpsense.domain.llm_capabilities import HAIKU, MAX_TOKENS, OPUS, Effort, TaskSettings
from serpsense.ports.drafts import DraftMaterial, DraftRow, NewDraft, SourceMention
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse
from serpsense.services.drafts import DAILY_THINKING, PROMPT_VERSION, Drafter, DraftPorts, Outcome
from serpsense.services.llm_gateway import LlmGateway
from tests.fakes import FixedClock, FixedProfiles, MemoryLedger, ScriptedLlm

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
OWNER, OLA, STORY = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
FARE, LATE = uuid.uuid4(), uuid.uuid4()
MATERIAL = DraftMaterial(
    owner_id=OWNER,
    brand_name="Ola",
    story_label="Extra cash at airport pickups",
    story_summary="Drivers ask riders for extra cash at airports.",
    mentions=(
        SourceMention(
            mention_id=FARE,
            source=MentionSource.PLAY_REVIEW,
            text="Asked ₹200 extra",
            url=None,
            sentiment=-1,
        ),
        SourceMention(
            mention_id=LATE,
            source=MentionSource.NEWS,
            text="Airport rides probed",
            url="https://n.in/a",
            sentiment=None,
        ),
    ),
)
SETTINGS = TaskSettings(OPUS, Effort.HIGH, 16000)


class Drafts:
    def __init__(self, material: DraftMaterial | None, thinking: int = 0) -> None:
        self.given, self.recorded, self.thinking = material, list[NewDraft](), thinking
        self.counted_since: list[datetime] = []

    def thinking_drafts_since(self, user_id: uuid.UUID, since: datetime) -> int:
        self.counted_since.append(since)
        return self.thinking

    def material(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID, *, limit: int
    ) -> DraftMaterial | None:
        return self.given if (user_id, brand_id, narrative_id) == (OWNER, OLA, STORY) else None

    def record(self, draft: NewDraft) -> uuid.UUID:
        self.recorded.append(draft)
        return uuid.UUID(int=7)

    def drafts(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID, *, limit: int
    ) -> list[DraftRow]:
        return []


class UnitOfWork:
    def __init__(self, drafts: Drafts) -> None:
        self.drafts, self.open = drafts, False

    def __call__(self) -> Self:
        return self

    def __enter__(self) -> Self:
        self.open = True
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:  # fmt: skip
        self.open = False


class Leases:
    """One holder at a time; `taken` pretends someone else holds every lease."""

    def __init__(self, *, taken: bool = False) -> None:
        self.taken, self.held = taken, list[str]()

    @contextmanager
    def hold(self, name: str) -> Iterator[bool]:
        self.held.append(name)
        yield not self.taken


class Thinking(ScriptedLlm):
    """A scripted model that also summarises its thinking when asked to."""

    def complete(self, request: LlmRequest) -> LlmResponse:
        response = super().complete(request)
        summary = "Checked which reviews mention airports." if request.reasoning_summary else None
        return replace(response, reasoning_summary=summary)


def drafter(
    material: DraftMaterial | None,
    answer: Callable[[LlmRequest], str | LlmCallFailed],
    store: Drafts | None = None,
    **ports: object,
) -> tuple[Drafter, Drafts, ScriptedLlm]:
    store = store or Drafts(material)
    uow = UnitOfWork(store)

    def checked(request: LlmRequest) -> str | LlmCallFailed:
        assert not uow.open  # no unit of work stays open while the model thinks
        return answer(request)

    llm = Thinking(checked)
    gateway = LlmGateway(
        llm, MemoryLedger(), FixedClock(NOW), monthly_budget_micros=lambda _: 10**9
    )
    given: dict[str, object] = {"profiles": FixedProfiles(SETTINGS), "leases": Leases()}
    given |= ports
    service = Drafter(DraftPorts(unit_of_work=uow, gateway=gateway, clock=FixedClock(NOW), **given))  # type: ignore[arg-type]  # the drafter's slice of a unit of work, and fakes
    return service, store, llm


def drafts(text: str, *cited: str) -> Callable[[LlmRequest], str]:
    return lambda request: json.dumps({"text": text, "cited": list(cited)})


def ask(service: Drafter, preset: DraftPreset = DraftPreset.STANDARD) -> Outcome:
    kind = DraftKind.REVIEW_REPLY
    return service.draft(OWNER, OLA, STORY, kind=kind, preset=preset).outcome


def test_a_draft_cites_the_mentions_it_answers_and_is_stored_with_provenance() -> None:
    service, store, llm = drafter(MATERIAL, drafts("We're sorry …", "m1", "m1"))
    result = service.draft(
        OWNER, OLA, STORY, kind=DraftKind.REVIEW_REPLY, preset=DraftPreset.STANDARD
    )
    assert (result.outcome, result.draft_id) == (Outcome.DRAFTED, uuid.UUID(int=7))
    (draft,) = store.recorded
    assert (draft.narrative_id, draft.kind, draft.preset, draft.text) == (
        STORY, DraftKind.REVIEW_REPLY, DraftPreset.STANDARD, "We're sorry …",
    )  # fmt: skip
    assert draft.cited == [FARE] and draft.reasoning_summary is None  # each mention once
    assert (draft.prompt_version, draft.llm_call_id, draft.created_at) == (
        PROMPT_VERSION, uuid.UUID(int=1), NOW,
    )  # fmt: skip
    (request,) = llm.requests
    assert (request.task, request.shape.effort) == (LlmTask.DRAFT_RESPONSE, Effort.HIGH)
    assert request.variables["kind"] == "review_reply" and request.variables["mentions"] == [
        {"id": "m1", "source": "play_review", "sentiment": "-1", "text": "Asked ₹200 extra"},
        {"id": "m2", "source": "news", "sentiment": "", "text": "Airport rides probed"},
    ]


@pytest.mark.parametrize(
    ("preset", "effort"), [(DraftPreset.HIGH_THINKING, Effort.XHIGH), (DraftPreset.MAX, Effort.MAX)]
)
def test_thinking_harder_raises_the_effort_and_keeps_a_reasoning_summary(
    preset: DraftPreset, effort: Effort
) -> None:
    service, store, llm = drafter(MATERIAL, drafts("We're looking into it.", "m1", "m2"))
    assert ask(service, preset) is Outcome.DRAFTED
    (request,) = llm.requests
    assert (request.shape.effort, request.reasoning_summary) == (effort, True)
    assert request.timeout_seconds > 90
    (draft,) = store.recorded
    assert draft.reasoning_summary == "Checked which reviews mention airports."
    assert draft.cited == [FARE, LATE] and draft.preset is preset


def test_a_draft_citing_an_id_it_was_not_shown_is_refused() -> None:
    service, store, _ = drafter(MATERIAL, drafts("We're sorry.", "m1", "m9"))
    assert ask(service) is Outcome.UNCITED and store.recorded == []


@pytest.mark.parametrize(
    "answer",
    [
        lambda request: LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0),
        drafts("We're sorry."),  # cites nothing
        drafts("", "m1"),
        drafts("Sorry\x00", "m1"),
    ],
)
def test_a_model_that_gives_nothing_usable_leaves_no_draft(
    answer: Callable[[LlmRequest], str | LlmCallFailed],
) -> None:
    service, store, _ = drafter(MATERIAL, answer)
    assert ask(service) is Outcome.FAILED and store.recorded == []


def test_a_strangers_or_empty_story_costs_no_call() -> None:
    service, _, llm = drafter(MATERIAL, drafts("x", "m1"))
    other = service.draft(
        uuid.uuid4(), OLA, STORY, kind=DraftKind.FAQ_ENTRY, preset=DraftPreset.MAX
    )
    assert other.outcome is Outcome.MISSING
    empty, _, quiet = drafter(replace(MATERIAL, mentions=()), drafts("x", "m1"))
    assert ask(empty) is Outcome.EMPTY and llm.requests == quiet.requests == []


def test_a_draft_is_one_attempt_and_its_invisible_characters_refuse_it() -> None:
    service, store, llm = drafter(MATERIAL, drafts(f"We're sorry{chr(0x200B)}.", "m1"))
    assert ask(service) is Outcome.FAILED and store.recorded == []
    (request,) = llm.requests
    assert request.max_retries == 0  # a person is waiting


def test_one_draft_at_a_time_per_user() -> None:
    leases = Leases(taken=True)
    service, store, llm = drafter(MATERIAL, drafts("x", "m1"), leases=leases)
    assert ask(service) is Outcome.BUSY and llm.requests == [] and store.recorded == []
    assert leases.held == [f"draft:{OWNER}"]


def test_thinking_hard_is_capped_per_day_and_standard_drafts_are_not() -> None:
    used = Drafts(MATERIAL, thinking=DAILY_THINKING)
    service, _, llm = drafter(MATERIAL, drafts("We're looking into it.", "m1"), store=used)
    for preset in (DraftPreset.HIGH_THINKING, DraftPreset.MAX):
        assert ask(service, preset) is Outcome.LIMITED
    assert llm.requests == [] and used.counted_since[0] == datetime(2026, 10, 4, tzinfo=UTC)
    assert ask(service) is Outcome.DRAFTED


def test_a_model_that_cant_think_that_hard_isnt_offered_it() -> None:
    haiku = FixedProfiles(TaskSettings(HAIKU, Effort.HIGH, MAX_TOKENS[LlmTask.DRAFT_RESPONSE]))
    service, _, llm = drafter(MATERIAL, drafts("x", "m1"), profiles=haiku)
    assert service.presets(OWNER) == (DraftPreset.STANDARD,)
    assert ask(service, DraftPreset.MAX) is Outcome.UNSUPPORTED and llm.requests == []
    opus, _, _ = drafter(MATERIAL, drafts("x", "m1"))
    assert opus.presets(OWNER) == tuple(DraftPreset)

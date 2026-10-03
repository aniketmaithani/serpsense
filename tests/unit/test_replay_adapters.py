"""Replay mode's search provider and model client: each scan's recorded answers by the clock,
and only the recorded model output, of the right brand and prompt, at no cost."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from serpsense.adapters.cache.null_cache import NullResponseCache
from serpsense.adapters.llm.replay import REPLAY_MODEL, ReplayLlm
from serpsense.adapters.replay.recording import (
    RecordedAnswer,
    RecordedLabel,
    RecordedScan,
    Recording,
    text_id,
)
from serpsense.adapters.serp.replay import ReplaySearchProvider
from serpsense.domain.enums import (
    LlmTask,
    MentionSource,
    SerpEngine,
    SerpErrorCode,
    ServedFrom,
    Topic,
)
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings
from serpsense.ports.llm_client import LlmCallFailed
from serpsense.ports.search_provider import SearchFailed, SearchRequest
from serpsense.services.labelling import Labels
from serpsense.services.llm_gateway import Call, LlmGateway
from tests.fakes import FixedClock, MemoryLedger

pytestmark = pytest.mark.unit

FIRST = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)
SECOND, THIRD = FIRST + timedelta(hours=12), FIRST + timedelta(hours=24)
NEWS = {"q": "Ola", "gl": "in", "hl": "en"}
SUGGEST = {"q": "ola ", "gl": "in", "hl": "en"}
LATE = "Ola driver cancelled twice"
BUDGET = 30_000_000  # US$30 a month, in micros: replayed calls cost nothing against it


def scan(at: datetime, *answers: tuple[SerpEngine, dict[str, str], int]) -> RecordedScan:
    found = tuple(RecordedAnswer(engine=e, params=p, payload=i) for e, p, i in answers)
    return RecordedScan(recorded_at=at, settings={}, answers=found)


def label(text: str, sentiment: int, prompt: str = "label_mentions/v1") -> RecordedLabel:
    return RecordedLabel(
        text_id=text_id(MentionSource.NEWS, text),
        prompt_version=prompt,
        is_about_brand=True,
        sentiment=sentiment,  # type: ignore[arg-type]  # -1, 0 or 1 in every call here
        topic=Topic.RELIABILITY,
        is_complaint=sentiment < 0,
        severity=70,
        reason=f"Recorded for {text}.",
    )


def recording(name: str = "Ola", *labels: RecordedLabel) -> Recording:
    payloads: tuple[dict[str, Any], ...] = ({"n": 1}, {"n": 2}, {"suggestions": []})
    news, suggest = SerpEngine.GOOGLE_NEWS, SerpEngine.GOOGLE_AUTOCOMPLETE
    return Recording(
        format=1,
        brand=name.lower(),
        name=name,
        payloads=payloads,
        scans=(
            scan(FIRST, (news, NEWS, 0)),
            scan(SECOND, (news, NEWS, 1), (suggest, SUGGEST, 2)),
            scan(THIRD, (news, NEWS, 1)),  # this one asked no suggestions
        ),
        labels=labels or (label(LATE, -1),),
    )


def answer_at(at: datetime, params: dict[str, str] = NEWS) -> Any:
    engine = SerpEngine.GOOGLE_NEWS if params is NEWS else SerpEngine.GOOGLE_AUTOCOMPLETE
    provider = ReplaySearchProvider([recording()], FixedClock(at))
    return provider.search(SearchRequest(engine, params, no_cache=True))


def test_a_search_gets_the_answer_recorded_by_the_scan_running_then() -> None:
    first = answer_at(FIRST + timedelta(milliseconds=3))
    assert (first.payload, first.served_from, first.http_status) == (
        {"n": 1},
        ServedFrom.SERPAPI_CACHE,  # never billed
        200,
    )
    assert answer_at(SECOND).payload == {"n": 2}
    assert answer_at(THIRD + timedelta(days=30)).payload == {"n": 2}  # Scan now: the last
    # A scan that didn't ask it repeats what the request got before.
    assert answer_at(THIRD, SUGGEST).payload == {"suggestions": []}


def test_a_search_nothing_was_recorded_for_by_then_fails_like_a_404() -> None:
    for at, params in ((FIRST, SUGGEST), (THIRD, {**NEWS, "q": "Uber"})):
        with pytest.raises(SearchFailed) as failed:
            answer_at(at, params)
        assert (failed.value.code, failed.value.http_status) == (SerpErrorCode.HTTP_4XX, 404)
        assert not failed.value.transient
    with pytest.raises(SearchFailed):
        answer_at(FIRST - timedelta(seconds=1))


def test_each_caller_gets_its_own_copy_and_the_null_cache_keeps_nothing() -> None:
    answer_at(SECOND, SUGGEST).payload["suggestions"].append("changed")
    assert answer_at(SECOND, SUGGEST).payload == {"suggestions": []}
    cache = NullResponseCache()
    cache.set("key", {"n": 1}, ttl=timedelta(hours=1))
    assert cache.get("key") is None


def label_call(brand: str, *texts: str, prompt: str = PROMPTS[LlmTask.LABEL_MENTIONS]) -> Call:
    mentions = [{"id": f"m{n}", "source": "news", "text": t} for n, t in enumerate(texts, 1)]
    return Call(
        task=LlmTask.LABEL_MENTIONS,
        prompt_version=prompt,
        variables={"brand": brand, "aliases": "", "not_the_brand": "", "mentions": mentions},
        settings=TaskSettings(OPUS, Effort.LOW, 8000),
        user_id=uuid.uuid4(),
    )


def replaying(ledger: MemoryLedger, *recordings: Recording) -> LlmGateway:
    llm = ReplayLlm(recordings or [recording()])
    return LlmGateway(llm, ledger, FixedClock(FIRST), monthly_budget_micros=lambda _: BUDGET)


def test_labels_are_the_recorded_ones_and_unrecorded_texts_are_left_out() -> None:
    ledger = MemoryLedger()
    answer = replaying(ledger).run(label_call("Ola", LATE, "Never recorded"), Labels)
    (recorded,) = answer.output.labels  # m2 is left out: it stays "not labelled yet"
    assert (recorded.id, recorded.sentiment, recorded.topic, recorded.reason) == (
        "m1",
        -1,
        Topic.RELIABILITY,
        f"Recorded for {LATE}.",
    )
    (call,) = ledger.calls
    assert (call.served_model, call.requested_model, call.cost_micros) == (REPLAY_MODEL, OPUS, 0)
    assert call.usage.input == call.usage.output == 0
    assert answer.served_model == REPLAY_MODEL


def test_a_text_two_brands_share_gets_each_brand_s_own_label() -> None:
    ola, uber = recording("Ola", label(LATE, -1)), recording("Uber", label(LATE, 1))
    gateway = replaying(MemoryLedger(), ola, uber)
    for brand, sentiment in (("Ola", -1), ("Uber", 1)):
        (got,) = gateway.run(label_call(brand, LATE), Labels).output.labels
        assert got.sentiment == sentiment
    assert gateway.run(label_call("Rapido", LATE), Labels).output.labels == []


def test_a_label_is_used_only_for_the_prompt_version_that_made_it() -> None:
    older = recording("Ola", label(LATE, -1, prompt="label_mentions/v2"))
    answer = replaying(MemoryLedger(), older).run(label_call("Ola", LATE), Labels)
    assert answer.output.labels == []


def test_a_task_with_nothing_recorded_fails_without_a_retry() -> None:
    call = label_call("Ola", LATE)
    explaining = replace(call, task=LlmTask.EXPLAIN_CRISIS, prompt_version="explain_crisis/v1")
    no_brand = replace(call, variables={"mentions": []})
    for unanswerable in (explaining, no_brand):
        with pytest.raises(LlmCallFailed) as failed:
            replaying(MemoryLedger()).run(unanswerable, Labels)
        assert failed.value.code == "llm.replay_unrecorded" and not failed.value.retryable

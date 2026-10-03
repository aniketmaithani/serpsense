"""Replay mode's search provider and model client: recorded answers, in order, at no cost."""

import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from serpsense.adapters.cache.null_cache import NullResponseCache
from serpsense.adapters.llm.replay import ReplayLlm
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

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
NEWS = {"q": "Ola", "gl": "in", "hl": "en"}
AUTOCOMPLETE = {"q": "ola ", "gl": "in", "hl": "en"}
LATE = "Ola driver cancelled twice"
BUDGET = 30_000_000  # US$30 a month, in micros: replayed calls cost nothing against it


def scan(hour: int, *answers: tuple[SerpEngine, dict[str, str], int]) -> RecordedScan:
    return RecordedScan(
        recorded_at=datetime(2026, 10, 3, hour, tzinfo=UTC),
        answers=tuple(RecordedAnswer(engine=e, params=p, payload=i) for e, p, i in answers),
    )


def recording() -> Recording:
    payloads: tuple[dict[str, Any], ...] = ({"n": 1}, {"n": 2}, {"suggestions": []})
    label = RecordedLabel(
        text_id=text_id(MentionSource.NEWS, LATE),
        prompt_version="label_mentions/v1",
        is_about_brand=True,
        sentiment=-1,
        topic=Topic.RELIABILITY,
        is_complaint=True,
        severity=70,
        reason="Rides cancelled.",
    )
    return Recording(
        format=1,
        brand="ola",
        payloads=payloads,
        scans=(
            scan(
                5,
                (SerpEngine.GOOGLE_NEWS, NEWS, 0),
                (SerpEngine.GOOGLE_AUTOCOMPLETE, AUTOCOMPLETE, 2),
            ),
            scan(17, (SerpEngine.GOOGLE_NEWS, NEWS, 1)),
        ),
        labels=(label,),
    )


def test_a_request_gets_each_recorded_answer_in_turn_then_the_last_again() -> None:
    served: Counter[str] = Counter()
    provider = ReplaySearchProvider([recording()], lambda request: served[request.params_hash])
    news = SearchRequest(SerpEngine.GOOGLE_NEWS, NEWS, no_cache=True)
    answers = []
    for _ in range(3):
        response = provider.search(news)
        served[news.params_hash] += 1  # as the ledger would count the successful call
        answers.append(response.payload["n"])
        assert (response.served_from, response.http_status) == (ServedFrom.SERPAPI_CACHE, 200)
    assert answers == [1, 2, 2]
    first = provider.search(SearchRequest(SerpEngine.GOOGLE_AUTOCOMPLETE, AUTOCOMPLETE))
    first.payload["suggestions"].append("changed")  # a caller's copy
    again = provider.search(SearchRequest(SerpEngine.GOOGLE_AUTOCOMPLETE, AUTOCOMPLETE))
    assert again.payload == {"suggestions": []}


def test_a_request_nothing_recorded_fails_like_a_404() -> None:
    provider = ReplaySearchProvider([recording()], lambda request: 0)
    with pytest.raises(SearchFailed) as failed:
        provider.search(SearchRequest(SerpEngine.GOOGLE_NEWS, {**NEWS, "q": "Uber"}))
    assert (failed.value.code, failed.value.http_status) == (SerpErrorCode.HTTP_4XX, 404)
    assert not failed.value.transient


def test_the_null_cache_keeps_nothing() -> None:
    cache = NullResponseCache()
    cache.set("key", {"n": 1}, ttl=timedelta(hours=1))
    assert cache.get("key") is None


def label_call(*texts: tuple[MentionSource, str]) -> Call:
    mentions = [{"id": f"m{n}", "source": s.value, "text": t} for n, (s, t) in enumerate(texts, 1)]
    return Call(
        task=LlmTask.LABEL_MENTIONS,
        prompt_version=PROMPTS[LlmTask.LABEL_MENTIONS],
        variables={"brand": "Ola", "aliases": "", "not_the_brand": "", "mentions": mentions},
        settings=TaskSettings(OPUS, Effort.LOW, 8000),
        user_id=uuid.uuid4(),
    )


def replaying(ledger: MemoryLedger) -> LlmGateway:
    llm = ReplayLlm([recording()])
    return LlmGateway(llm, ledger, FixedClock(NOW), monthly_budget_micros=lambda _: BUDGET)


def test_labelling_is_answered_with_the_recorded_labels_at_no_cost() -> None:
    ledger = MemoryLedger()
    gateway = replaying(ledger)
    texts = ((MentionSource.NEWS, LATE), (MentionSource.SERP_RESULT, LATE))
    answer = gateway.run(label_call(*texts), Labels)
    recorded, unknown = answer.output.labels
    assert (recorded.id, recorded.sentiment, recorded.topic, recorded.is_about_brand) == (
        "m1",
        -1,
        Topic.RELIABILITY,
        True,
    )
    assert recorded.reason == "Rides cancelled." and unknown.id == "m2"
    assert not unknown.is_about_brand and "no label was recorded" in unknown.reason
    (call,) = ledger.calls
    assert (call.cost_micros, call.served_model, call.usage.input) == (0, OPUS, 0)


def test_a_task_with_nothing_recorded_fails_without_a_retry() -> None:
    gateway = replaying(MemoryLedger())
    call = label_call((MentionSource.NEWS, LATE))
    other = replace(call, task=LlmTask.GROUP_NARRATIVES, prompt_version="group_narratives/v1")
    with pytest.raises(LlmCallFailed) as failed:
        gateway.run(other, Labels)
    assert failed.value.code == "llm.replay_unrecorded" and not failed.value.retryable

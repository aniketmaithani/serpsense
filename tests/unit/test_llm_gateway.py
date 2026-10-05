"""The LLM gateway with a scripted client and an in-memory ledger (BUILD_PLAN §7.4)."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import BaseModel
from structlog.testing import capture_logs

from serpsense.domain.enums import LlmCallOutcome, LlmTask
from serpsense.domain.llm_capabilities import (
    HAIKU,
    OPUS,
    SONNET,
    Effort,
    TaskSettings,
    UnsupportedSetting,
)
from serpsense.domain.llm_pricing import Hop, TokenUsage, cost_micros
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse
from serpsense.ports.llm_ledger import LlmCallRecord
from serpsense.services.llm_gateway import (
    Answer,
    Call,
    LlmBudgetExhausted,
    LlmGateway,
    LlmOutputRejected,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
USER = uuid.uuid4()
USAGE = TokenUsage(input=1000, output=500, cache_read=0, cache_write=0)  # $0.014 on Opus
SECRET = "late driver, call 98xxxx"  # mention text: must never reach a log


class Label(BaseModel):
    id: str
    sentiment: Literal[-1, 0, 1]


class Labels(BaseModel):
    labels: list[Label]


class Scripted:
    def __init__(self, answer: LlmResponse | LlmCallFailed) -> None:
        self.answer, self.requests = answer, list[LlmRequest]()

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if isinstance(self.answer, LlmCallFailed):
            raise self.answer
        return self.answer


class Ledger:
    def __init__(self, spent: int = 0) -> None:
        self.spent, self.calls = spent, list[LlmCallRecord]()

    def record(self, call: LlmCallRecord) -> uuid.UUID:
        self.calls.append(call)
        return uuid.UUID(int=len(self.calls))

    def spent_since(self, user_id: uuid.UUID, since: datetime) -> int:
        assert since == datetime(2026, 10, 1, tzinfo=UTC)  # the month so far, in UTC
        return self.spent


class Clock:
    def now(self) -> datetime:
        return NOW


def response(**overrides: object) -> LlmResponse:
    values: dict[str, object] = {
        "served_model": OPUS,
        "stop_reason": "end_turn",
        "output": '{"labels": [{"id": "m1", "sentiment": -1}]}',
        "hops": (Hop(OPUS, USAGE),),
        "latency_ms": 2100,
    }
    return LlmResponse(**{**values, **overrides})  # type: ignore[arg-type]  # test builder


def gateway(client: Scripted, ledger: Ledger, budget: int = 30_000_000) -> LlmGateway:
    return LlmGateway(client, ledger, Clock(), monthly_budget_micros=lambda user: budget)


CALL = Call(
    task=LlmTask.LABEL_MENTIONS,
    prompt_version="label_mentions/v1",
    variables={"brand": "Ola", "mentions": [{"id": "m1", "text": SECRET}]},
    settings=TaskSettings(OPUS, Effort.LOW, 8000),
    user_id=USER,
    scan_id=uuid.uuid4(),
)


def test_a_successful_call_is_recorded_and_returns_typed_output() -> None:
    client, ledger = Scripted(response()), Ledger()
    with capture_logs() as logs:
        answer = gateway(client, ledger).run(CALL, Labels)
    labels = Labels(labels=[Label(id="m1", sentiment=-1)])
    assert answer == Answer(uuid.UUID(int=1), "label_mentions/v1", labels, OPUS, None)
    ((request,), (record,)) = client.requests, ledger.calls
    assert (request.output_schema, request.shape.effort) == (Labels.model_json_schema(), Effort.LOW)
    got = (record.outcome, record.cost_micros, record.served_model, record.scan_id)
    assert (
        got == (LlmCallOutcome.SUCCEEDED, 14_000, OPUS, CALL.scan_id) and record.created_at == NOW
    )
    assert record.request_settings["effort"] == "low"
    json.dumps(record.request_settings)  # what the jsonb column will hold
    assert SECRET not in repr(logs)  # prompts and variables are never logged


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (response(stop_reason="refusal", output=None), LlmCallOutcome.REFUSED),
        (response(stop_reason="max_tokens", output='{"labels": ['), LlmCallOutcome.TRUNCATED),
        (
            response(output='{"labels": [{"id": "m1", "sentiment": 7}]}'),
            LlmCallOutcome.INVALID_OUTPUT,
        ),
        (response(output="not json"), LlmCallOutcome.INVALID_OUTPUT),
        (response(stop_reason="pause_turn"), LlmCallOutcome.INVALID_OUTPUT),  # nothing finished
        (response(stop_reason="model_context_window_exceeded"), LlmCallOutcome.TRUNCATED),
    ],
)
def test_an_unusable_answer_is_recorded_and_rejected(
    answer: LlmResponse, outcome: LlmCallOutcome
) -> None:
    ledger = Ledger()
    with pytest.raises(LlmOutputRejected) as exc:
        gateway(Scripted(answer), ledger).run(CALL, Labels)
    assert (exc.value.outcome, exc.value.call_id) == (outcome, uuid.UUID(int=1))
    assert [record.outcome for record in ledger.calls] == [outcome]


def test_a_failed_call_is_recorded_at_no_cost_and_raised() -> None:
    ledger = Ledger()
    failure = LlmCallFailed("llm.network", retryable=True, latency_ms=600)
    with pytest.raises(LlmCallFailed):
        gateway(Scripted(failure), ledger).run(CALL, Labels)
    (record,) = ledger.calls
    assert (record.outcome, record.served_model, record.cost_micros, record.latency_ms) == (
        LlmCallOutcome.FAILED,
        None,
        0,
        600,
    )


def test_a_timed_out_call_counts_at_its_worst_case() -> None:
    ledger = Ledger()
    failure = LlmCallFailed("llm.timeout", retryable=True, latency_ms=60_000)
    with pytest.raises(LlmCallFailed):
        gateway(Scripted(failure), ledger).run(replace(CALL, max_retries=0), Labels)
    (record,) = ledger.calls
    worst = cost_micros(CALL.settings.model, TokenUsage(0, CALL.settings.max_tokens, 0, 0))
    assert (record.outcome, record.cost_micros) == (LlmCallOutcome.FAILED, worst)
    assert worst > 0 and record.request_settings["max_retries"] == 0


class SharedLedger(Ledger):
    """Every user's spend today, against the global daily cap."""

    def __init__(self, today: int) -> None:
        super().__init__()
        self.today = today

    def spent_in_all_since(self, since: datetime) -> int:
        assert since == datetime(2026, 10, 3, tzinfo=UTC)  # the UTC day so far
        return self.today


def test_the_daily_cap_across_all_users_stops_the_call_before_it_is_made() -> None:
    client, ledger = Scripted(response()), SharedLedger(today=5_000_000)
    capped = LlmGateway(
        client, ledger, Clock(), monthly_budget_micros=lambda user: 30_000_000,
        daily_cap_micros=5_000_000,
    )  # fmt: skip
    with capture_logs() as logs, pytest.raises(LlmBudgetExhausted):
        capped.run(CALL, Labels)
    assert client.requests == [] and ledger.calls == []  # nothing asked, nothing recorded
    assert [log["event"] for log in logs] == ["llm_budget.global_exhausted"]
    ledger.today = 4_999_999
    assert capped.run(CALL, Labels).call_id == uuid.UUID(int=1)


def test_a_spent_budget_stops_the_call_before_it_is_made() -> None:
    client, ledger = Scripted(response()), Ledger(spent=30_000_000)
    with pytest.raises(LlmBudgetExhausted):
        gateway(client, ledger).run(CALL, Labels)
    assert client.requests == [] and ledger.calls == []
    ledger.spent = 29_999_999  # one micro left: the call goes ahead
    gateway(client, ledger).run(CALL, Labels)
    assert len(ledger.calls) == 1


def test_unsupported_settings_are_refused_before_anything_is_spent() -> None:
    client, ledger = Scripted(response()), Ledger()
    bad = Call(**{**CALL.__dict__, "settings": TaskSettings(HAIKU, Effort.MAX, 1024)})
    with pytest.raises(UnsupportedSetting):
        gateway(client, ledger).run(bad, Labels)
    assert client.requests == [] and ledger.calls == []


def test_every_model_that_ran_is_billed_at_its_own_price() -> None:
    ledger = Ledger()
    declined, answered = Hop(OPUS, USAGE), Hop("claude-opus-4-8", USAGE)  # $5/$25 per MTok
    fallback = response(served_model="claude-opus-4-8", hops=(declined, answered))
    gateway(Scripted(fallback), ledger).run(CALL, Labels)
    assert ledger.calls[0].cost_micros == 14_000 + 17_500
    gateway(Scripted(response(served_model=SONNET, hops=(Hop(SONNET, USAGE),))), ledger).run(
        CALL, Labels
    )
    assert ledger.calls[1].cost_micros == 7_000  # Sonnet's own price, not the requested Opus's
    unknown = response(served_model="claude-next", hops=(Hop("claude-next", USAGE),))
    with capture_logs() as logs:
        gateway(Scripted(unknown), ledger).run(CALL, Labels)
    assert ledger.calls[2].cost_micros == 17_500  # no price: charged at the dearest known
    assert [entry["event"] for entry in logs] == ["llm_price.substituted", "llm_call.recorded"]

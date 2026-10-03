"""The LLM gateway: every model call goes through it (BUILD_PLAN §7.4, ADR-0008).

Settings are checked against the model, the user's monthly budget is checked, the call is made,
its stop reason classified and its structured output validated, and the call is priced on the
model that served it and recorded before anything uses the output. The model labels, groups,
explains and drafts; nothing it returns is acted on without a deterministic rule or a person.
Prompts, variables and completions are never logged: a call is referred to by its id and
prompt version. The budget is checked before each call, so concurrent calls can overshoot it by
at most one call each, a bound that suits a monthly cap. A call that timed out may still have run
and been billed, so it is recorded at its worst case, max_tokens at the model's output price, and
counts against the budget like one that answered.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from serpsense.domain.enums import LlmCallOutcome, LlmTask
from serpsense.domain.llm_capabilities import RequestShape, TaskSettings, request_shape
from serpsense.domain.llm_pricing import (
    CURRENCY,
    PRICES,
    Hop,
    TokenUsage,
    UnknownModel,
    cost_micros,
)
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.llm_client import LlmCallFailed, LLMClient, LlmRequest, LlmResponse, Variables
from serpsense.ports.llm_ledger import LlmCallRecord, LlmLedger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)
NO_TOKENS = TokenUsage(input=0, output=0, cache_read=0, cache_write=0)
DEAREST = "dearest_known"
FINISHED = frozenset({"end_turn", "stop_sequence"})
TIMED_OUT = "llm.timeout"
STOPPED = {
    "refusal": LlmCallOutcome.REFUSED,
    "max_tokens": LlmCallOutcome.TRUNCATED,
    "model_context_window_exceeded": LlmCallOutcome.TRUNCATED,
}


@dataclass(frozen=True)
class Call:
    task: LlmTask
    prompt_version: str
    variables: Variables = field(repr=False)
    settings: TaskSettings
    user_id: uuid.UUID
    scan_id: uuid.UUID | None = None
    prompt_caching: bool = True
    refusal_fallback: bool = True
    timeout_seconds: float = 60.0
    reasoning_summary: bool = False
    max_retries: int | None = None  # None: the client's default


@dataclass(frozen=True)
class Answer(Generic[T]):
    call_id: uuid.UUID  # the provenance every output row carries, with the prompt version
    prompt_version: str
    output: T = field(repr=False)
    served_model: str
    reasoning_summary: str | None = field(repr=False)


@dataclass(frozen=True)
class _Ended:
    """How a call ended: its response (none if it failed), outcome and latency."""

    response: LlmResponse | None
    outcome: LlmCallOutcome
    latency_ms: int
    assumed_cost_micros: int = 0  # for a call with no response that may have been billed


class LlmBudgetExhausted(Exception):
    """The user's monthly model budget is spent: a hard stop (ADR-0008)."""


class LlmOutputRejected(Exception):
    """The call was made and recorded, but gave nothing usable: refused, truncated or invalid."""

    def __init__(self, outcome: LlmCallOutcome, call_id: uuid.UUID) -> None:
        super().__init__(outcome.value)
        self.outcome = outcome
        self.call_id = call_id


class LlmGateway:
    def __init__(
        self,
        client: LLMClient,
        ledger: LlmLedger,
        clock: Clock,
        *,
        monthly_budget_micros: Callable[[uuid.UUID], int],
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._clock = clock
        self._budget = monthly_budget_micros

    def run(self, call: Call, output: type[T]) -> Answer[T]:
        shape = request_shape(call.settings)  # UnsupportedSetting before anything is spent
        now = self._clock.now()
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if self._ledger.spent_since(call.user_id, month) >= self._budget(call.user_id):
            log.warning("llm_budget.exhausted", user_id=str(call.user_id), task=call.task.value)
            raise LlmBudgetExhausted(call.task.value)
        request = _request(call, shape, output)
        try:
            response = self._client.complete(request)
        except LlmCallFailed as exc:
            worst = _worst_case(shape) if exc.code == TIMED_OUT else 0
            ended = _Ended(None, LlmCallOutcome.FAILED, exc.latency_ms, worst)
            self._record(call, shape, now, ended)
            raise
        outcome, parsed = _classify(response, output)
        call_id = self._record(call, shape, now, _Ended(response, outcome, response.latency_ms))
        if parsed is None:
            raise LlmOutputRejected(outcome, call_id)
        summary = response.reasoning_summary
        return Answer(call_id, call.prompt_version, parsed, response.served_model, summary)

    def _record(
        self,
        call: Call,
        shape: RequestShape,
        now: datetime,
        ended: _Ended,
    ) -> uuid.UUID:
        response, outcome, latency_ms = ended.response, ended.outcome, ended.latency_ms
        usage = response.usage if response else NO_TOKENS
        served = response.served_model if response else None
        record = LlmCallRecord(
            user_id=call.user_id,
            scan_id=call.scan_id,
            task=call.task,
            requested_model=shape.model,
            served_model=served,
            prompt_version=call.prompt_version,
            request_settings=_settings(call, shape),
            usage=usage,
            cost_micros=_cost(response.hops) if response else ended.assumed_cost_micros,
            currency=CURRENCY,
            stop_reason=response.stop_reason if response else None,
            outcome=outcome,
            latency_ms=latency_ms,
            created_at=now,
        )
        call_id = self._ledger.record(record)
        log.info(
            "llm_call.recorded",
            llm_call_id=str(call_id),
            task=call.task.value,
            prompt_version=call.prompt_version,
            outcome=outcome.value,
            served_model=served,
            cost_micros=record.cost_micros,
            latency_ms=latency_ms,
        )
        return call_id


def _request(call: Call, shape: RequestShape, output: type[BaseModel]) -> LlmRequest:
    return LlmRequest(
        task=call.task,
        prompt_version=call.prompt_version,
        variables=call.variables,
        shape=shape,
        output_schema=output.model_json_schema(),
        prompt_caching=call.prompt_caching,
        refusal_fallback=call.refusal_fallback,
        timeout_seconds=call.timeout_seconds,
        reasoning_summary=call.reasoning_summary,
        max_retries=call.max_retries,
    )


def _classify(response: LlmResponse, output: type[T]) -> tuple[LlmCallOutcome, T | None]:
    if response.stop_reason not in FINISHED:  # pause_turn, tool_use: nothing finished to read
        return STOPPED.get(response.stop_reason, LlmCallOutcome.INVALID_OUTPUT), None
    try:
        return LlmCallOutcome.SUCCEEDED, output.model_validate_json(response.output or "")
    except ValidationError:
        return LlmCallOutcome.INVALID_OUTPUT, None


def _cost(hops: tuple[Hop, ...]) -> int:
    """Every model that ran is billed, a declined first attempt included."""
    total = 0
    for hop in hops:
        try:
            total += cost_micros(hop.model, hop.usage)
        except UnknownModel:
            # A model with no price here is charged at the dearest price known rather than
            # as free or as a cheaper model, and said so.
            log.warning("llm_price.substituted", model=hop.model, priced_as=DEAREST)
            total += max(cost_micros(model, hop.usage) for model in PRICES)
    return total


def _worst_case(shape: RequestShape) -> int:
    """What a timed-out call could have cost: all its output tokens, at the model's price."""
    return _cost((Hop(shape.model, TokenUsage(input=0, output=shape.max_tokens, cache_read=0,
                                               cache_write=0)),))  # fmt: skip


def _settings(call: Call, shape: RequestShape) -> dict[str, Any]:
    return {
        "model": shape.model,
        "max_tokens": shape.max_tokens,
        "effort": shape.effort.value if shape.effort else None,
        "thinking": shape.thinking,
        "temperature": shape.temperature,
        "prompt_caching": call.prompt_caching,
        "refusal_fallback": call.refusal_fallback,
        "reasoning_summary": call.reasoning_summary,
        "timeout_seconds": call.timeout_seconds,
        "max_retries": call.max_retries,
    }

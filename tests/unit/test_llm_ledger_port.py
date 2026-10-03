"""An LLM call record is checked before it reaches the ledger (data-model §6)."""

import uuid
from datetime import UTC, datetime

import pytest

from serpsense.domain.enums import LlmCallOutcome, LlmTask
from serpsense.domain.llm_pricing import TokenUsage
from serpsense.ports.llm_ledger import LlmCallRecord

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, tzinfo=UTC)


def record(**overrides: object) -> LlmCallRecord:
    values: dict[str, object] = {
        "user_id": uuid.uuid4(),
        "scan_id": None,
        "task": LlmTask.LABEL_MENTIONS,
        "requested_model": "claude-opus-5-5",
        "served_model": "claude-opus-5-5",
        "prompt_version": "label_mentions/v1",
        "request_settings": {},
        "usage": TokenUsage(input=1, output=1, cache_read=0, cache_write=0),
        "cost_micros": 24,
        "currency": "USD",
        "stop_reason": "end_turn",
        "outcome": LlmCallOutcome.SUCCEEDED,
        "latency_ms": 10,
        "created_at": NOW,
    }
    return LlmCallRecord(**{**values, **overrides})  # type: ignore[arg-type]  # test builder


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": LlmCallOutcome.FAILED},  # a failed call has no served model
        {"served_model": None},
        {"prompt_version": "draft_response/v1"},
        {"cost_micros": -1},
        {"latency_ms": -1},
        {"created_at": datetime(2026, 10, 3)},  # noqa: DTZ001  # deliberately naive
    ],
)
def test_a_record_the_ledger_would_reject_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        record(**overrides)


def test_a_valid_record_and_a_failed_call_are_accepted() -> None:
    assert record().cost_micros == 24
    failed = record(outcome=LlmCallOutcome.FAILED, served_model=None, stop_reason=None)
    assert failed.served_model is None

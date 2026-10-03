"""The eval runner: golden items through the gateway, agreement by field and class, scored as
the product stores labels, and runs stopped by the cap or by failures."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.services.evals import (
    Evaluator,
    GoldenBrand,
    GoldenItem,
    MemoryLedger,
    Stop,
    report,
)
from serpsense.services.llm_gateway import LlmGateway
from tests.fakes import FixedClock, ScriptedLlm
from tests.unit.test_scans import NOW

pytestmark = pytest.mark.unit

SETTINGS = TaskSettings(OPUS, Effort.LOW, 8000)
BRAND = GoldenBrand(name="Ola", aliases="Ola Cabs", not_the_brand="Ola Electric")
TIMEOUT = LlmCallFailed("llm.timeout", retryable=True, latency_ms=5)


def item(n: int, sentiment: int = -1, *, about: bool = True) -> GoldenItem:
    expected = {
        "is_about_brand": about,
        "sentiment": sentiment if about else 0,
        "severity": 30,
        "topic": "reliability" if about else "other",
        "is_complaint": about and sentiment < 0,
    }
    row = {"id": f"g{n:03}", "source": "play_review", "text": f"text {n}", "split": "test"}
    return GoldenItem.model_validate(row | {"expected": expected})


def mentions(request: LlmRequest) -> Sequence[Mapping[str, str]]:
    records = request.variables["mentions"]
    assert not isinstance(records, str)
    return records


def answering(
    items: Sequence[GoldenItem], change: Mapping[str, Any] | None = None
) -> Callable[[LlmRequest], str]:
    """A model that answers every golden label as expected, but for `change` on g001."""
    golden = {i.id: i.expected.model_dump() for i in items}

    def answer(request: LlmRequest) -> str:
        labels = []
        for record in mentions(request):
            label = {"id": record["id"], **golden[record["id"]], "reason": "Because."}
            if record["id"] == "g001" and change:
                label |= change
            labels.append(label)
        return json.dumps({"labels": labels})

    return answer


def evaluator(answer: Callable[[LlmRequest], str | LlmCallFailed]) -> tuple[Evaluator, ScriptedLlm]:
    llm, ledger = ScriptedLlm(answer), MemoryLedger()
    gateway = LlmGateway(llm, ledger, FixedClock(NOW), monthly_budget_micros=lambda _: 2**62)
    return Evaluator(gateway, ledger, SETTINGS), llm


def test_perfect_answers_agree_on_every_field_and_class() -> None:
    items = [item(1), item(2, 0), item(3, 1), item(4, about=False)]
    run, llm = evaluator(answering(items))
    result = run.label_mentions(items, BRAND, cap_micros=10**9)
    assert (result.items, result.answered, result.disagreements) == (4, 4, [])
    assert result.rate("is_about_brand") == result.rate("sentiment") == 1.0
    assert (result.counted["sentiment"], result.counted["sentiment[-1]"]) == (3, 1)  # about only
    sent = llm.requests[0].variables
    assert (sent["brand"], sent["aliases"], sent["not_the_brand"]) == (
        "Ola",
        "Ola Cabs",
        "Ola Electric",
    )
    assert result.cost_micros > 0 and len(result.calls) == 1


def test_a_wrong_answer_is_listed_by_field() -> None:
    items = [item(1), item(2)]
    run, _ = evaluator(answering(items, {"sentiment": 0, "severity": 90}))
    result = run.label_mentions(items, BRAND, cap_micros=10**9)
    assert result.rate("sentiment") == 0.5 and result.rate("sentiment[-1]") == 0.5
    assert {(d.item_id, d.field) for d in result.disagreements} == {
        ("g001", "sentiment"),
        ("g001", "severity±15"),
    }


def test_a_label_for_a_text_the_model_calls_unrelated_is_scored_as_stored() -> None:
    items = [item(1)]  # about the brand, negative
    run, _ = evaluator(answering(items, {"is_about_brand": False}))  # its sentiment kept at -1
    result = run.label_mentions(items, BRAND, cap_micros=10**9)
    assert result.rate("is_about_brand") == 0 and result.rate("sentiment") == 0  # stored as 0


def test_a_failed_batch_is_unanswered() -> None:
    items = [item(n) for n in range(1, 6)]
    run, _ = evaluator(lambda request: TIMEOUT)
    failed = run.label_mentions(items, BRAND, cap_micros=10**9)
    assert (failed.answered, failed.rate("answered"), failed.stopped) == (0, 0.0, None)
    assert {d.field for d in failed.disagreements} == {"answered"}


def test_two_failed_batches_in_a_row_stop_the_run() -> None:
    items = [item(n) for n in range(1, 76)]  # three batches of 25
    run, llm = evaluator(lambda request: TIMEOUT)
    result = run.label_mentions(items, BRAND, cap_micros=10**9)
    assert result.stopped is Stop.FAILURES and len(llm.requests) == 2
    assert result.counted["answered"] == 75 and result.answered == 0


def test_a_failure_no_retry_fixes_stops_the_run_at_once() -> None:
    items = [item(n) for n in range(1, 51)]
    bad_key = LlmCallFailed("llm.auth", retryable=False, latency_ms=5)
    run, llm = evaluator(lambda request: bad_key)
    assert run.label_mentions(items, BRAND, cap_micros=10**9).stopped is Stop.FAILURES
    assert len(llm.requests) == 1


def test_the_cap_stops_the_run_before_a_call_that_would_pass_it() -> None:
    items = [item(n) for n in range(1, 61)]  # three batches
    run, llm = evaluator(answering(items))
    result = run.label_mentions(items, BRAND, cap_micros=1)  # any first call passes it
    assert result.stopped is Stop.CAP and len(llm.requests) == 1 and result.answered == 25
    again = run.label_mentions(items[:25], BRAND, cap_micros=10**9)
    assert len(again.calls) == 1 and again.cost_micros == result.cost_micros  # its own calls


def test_the_report_states_agreement_cost_and_disagreements() -> None:
    items = [item(1), item(2, 1)]
    run, _ = evaluator(answering(items, {"is_about_brand": False}))
    text = report(
        run.label_mentions(items, BRAND, cap_micros=10**9),
        on="2026-10-03",
        model=OPUS,
        split="test",
    )
    assert "| answered | 2/2 | 100.0% |" in text and "| is_about_brand | 1/2 | 50.0% |" in text
    assert "| g001 | is_about_brand | True | False |" in text and "Cost: $" in text
    assert "| g001 | topic | reliability | other |" in text  # enum values, as stored


def test_the_report_says_why_a_run_stopped() -> None:
    run, _ = evaluator(lambda request: TIMEOUT)
    result = run.label_mentions([item(n) for n in range(1, 51)], BRAND, cap_micros=10**9)
    text = report(result, on="2026-10-03", model=OPUS, split="test")
    assert "**Stopped after failed calls**" in text and "| answered | 0/50 | 0.0% |" in text

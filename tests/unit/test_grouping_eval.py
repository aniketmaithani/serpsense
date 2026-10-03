"""The grouping eval: golden mentions placed as the grouper would, scored by pairs and by joining
the open narrative, and runs stopped by the cap or a failure."""

import json
from collections.abc import Callable, Mapping

import pytest

from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.services.evals import MemoryLedger, Stop
from serpsense.services.grouping import BATCH
from serpsense.services.grouping_eval import (
    GoldenMention,
    GroupingBrand,
    GroupingEvaluator,
    GroupingResult,
    f1,
    grouping_report,
)
from serpsense.services.llm_gateway import LlmGateway
from tests.fakes import FixedClock, ScriptedLlm
from tests.unit.test_scans import NOW

pytestmark = pytest.mark.unit

SETTINGS = TaskSettings(OPUS, Effort.MEDIUM, 8000)
OPEN = {"story": "cash", "label": "Drivers demand cash", "summary": "Riders say so.", "mentions": 6}
BRAND = GroupingBrand.model_validate({"name": "Ola", "aliases": "Ola Cabs", "open": [OPEN]})


def mention(n: int, story: str | None) -> GoldenMention:
    row = {"id": f"n{n:03}", "source": "play_review", "topic": "pricing", "severity": 30}
    return GoldenMention.model_validate(
        row | {"text": f"text {n}", "story": story, "split": "test"}
    )


def placing(where: Mapping[str, str | None]) -> Callable[[LlmRequest], str]:
    """A model that places each mention by its text, and starts one story."""

    def answer(request: LlmRequest) -> str:
        records = request.variables["mentions"]
        assert not isinstance(records, str)
        placed = [{"id": r["id"], "narrative": where.get(r["text"])} for r in records]
        story = {"id": "new1", "label": "Refunds pending", "summary": "Riders wait for refunds."}
        return json.dumps({"new_narratives": [story], "placements": placed})

    return answer


def grouping_evaluator(
    answer: Callable[[LlmRequest], object],
) -> tuple[GroupingEvaluator, ScriptedLlm]:
    ledger, llm = MemoryLedger(), ScriptedLlm(answer)  # type: ignore[arg-type]
    gateway = LlmGateway(llm, ledger, FixedClock(NOW), monthly_budget_micros=lambda _: 2**62)
    return GroupingEvaluator(gateway, ledger, SETTINGS), llm


def run(
    items: list[GoldenMention], answer: Callable[[LlmRequest], object], cap_micros: int = 10**9
) -> tuple[GroupingResult, ScriptedLlm]:
    evaluator, llm = grouping_evaluator(answer)
    return evaluator.group_narratives(items, BRAND, cap_micros=cap_micros), llm


def test_placements_are_scored_by_pairs_and_by_joining_the_open_narrative() -> None:
    items = [mention(1, "refunds"), mention(2, "refunds"), mention(3, None), mention(4, "cash")]
    where = {"text 1": "new1", "text 2": "new1", "text 3": "new1", "text 4": "n1"}
    result, llm = run(items, placing(where))
    assert result.placed == {"n001": "new1", "n002": "new1", "n003": "new1", "n004": "cash"}
    assert result.pairs() == (1, 3, 1)  # one right pair, two wrong ones with the one-off
    assert result.joined_open({"cash"}) == (1, 1) and result.started == ["Refunds pending"]
    sent = llm.requests[0].variables
    assert sent["narratives"] == [
        {"id": "n1", "mentions": "6", "label": "Drivers demand cash", "text": "Riders say so."}
    ]
    text = grouping_report(result, BRAND, on="2026-10-03", model="claude-opus-5-5")
    assert "| Precision | 33.3% |" in text and "| Recall | 100.0% |" in text
    assert "| F1 | 50.0% |" in text and "| Joined the open narrative | 1/1 |" in text
    assert "- new1: Refunds pending" in text and "| n003 | normal | - | new1 |" in text


def test_one_offs_left_alone_count_and_set_aside_placements_are_reported() -> None:
    items = [mention(1, "refunds"), mention(2, "refunds"), mention(3, None), mention(4, None)]
    result, _ = run(items, placing({"text 1": "n9", "text 4": "new1"}))  # n9: no such narrative
    assert result.placed == {"n002": None, "n003": None, "n004": "new1"}
    assert (result.dropped, result.ignored) == (["n001"], 0)  # not scored as a one-off
    assert result.pairs() == (0, 0, 0) and result.one_offs() == (1, 2)
    text = grouping_report(result, BRAND, on="2026-10-03", model="claude-opus-5-5")
    assert (
        "| One-offs left alone | 1/2 |" in text and "| n001 | normal | refunds | dropped |" in text
    )
    assert "| Placements set aside (items dropped, answers ignored) | 1, 0 |" in text


@pytest.mark.parametrize(
    ("precision", "recall", "expected"),
    [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.5, 1.0, 2 / 3), (None, 1.0, None), (1.0, None, None)],
)
def test_f1_is_defined_whenever_both_rates_are(
    precision: float | None, recall: float | None, expected: float | None
) -> None:
    assert f1(precision, recall) == expected


def test_the_cap_or_a_failure_stops_the_run() -> None:
    items = [mention(n, None) for n in range(BATCH + 1)]
    capped, llm = run(items, placing({}), cap_micros=1)
    assert capped.stopped is Stop.CAP and capped.answered == BATCH and len(llm.requests) == 1
    timeout = LlmCallFailed("llm.timeout", retryable=True, latency_ms=5)
    failed, _ = run(items, lambda request: timeout)
    assert failed.stopped is Stop.FAILURES and failed.answered == 0 and len(failed.calls) == 1
    text = grouping_report(failed, BRAND, on="2026-10-03", model="claude-opus-5-5")
    assert "**Stopped** (failures)" in text and "| Precision | - |" in text

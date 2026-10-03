"""Score a prompt against its golden set (AGENTS §7, ADR-0008).

A run sends the set's items through the real gateway in the product's batches, with an
in-memory ledger: nothing is stored, and every call's cost and latency are counted. A spend cap
stops the run before a call that would likely pass it (judged by the dearest call so far), so a
run never overspends by more than one call; like the product, two failed batches in a row or a
failure no retry can fix stop it too. Answers are scored as the product would store them (a text
the model calls unrelated gets fixed values), with agreement per field and per class, cost per
item and latency.
"""

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from serpsense.domain.enums import LlmTask, Topic
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.llm_capabilities import TaskSettings
from serpsense.ports.llm_client import LlmCallFailed
from serpsense.ports.llm_ledger import LlmCallRecord
from serpsense.services.labelling import BATCH, STOP_AFTER, Label, Labels
from serpsense.services.llm_gateway import Call, LlmGateway, LlmOutputRejected

EVAL_USER = uuid.UUID(int=0)  # evals bill no one; the ledger is in memory
SEVERITY_SLACK = 15  # severities this close agree


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"


class Stop(StrEnum):
    CAP = "cap"  # the next call would likely pass the spend cap
    FAILURES = "failures"  # failed batches in a row, or a failure no retry fixes


class GoldenLabel(BaseModel):
    is_about_brand: bool
    sentiment: Literal[-1, 0, 1]
    severity: int
    topic: Topic
    is_complaint: bool


class GoldenItem(BaseModel):
    """One hand-checked mention of `evals/golden/label_mentions.jsonl`."""

    id: str
    source: str
    language: str | None = None
    text: str
    expected: GoldenLabel
    split: Split
    difficulty: str = "normal"


class GoldenBrand(BaseModel):
    """The brand a golden set describes (`<task>.brand.json`); its README fixes these values."""

    name: str
    aliases: str = ""
    not_the_brand: str = ""


@dataclass(frozen=True)
class Disagreement:
    item_id: str
    field: str
    golden: object
    model: object


class MemoryLedger:
    """Counts calls instead of storing them; evals have no monthly budget."""

    def __init__(self) -> None:
        self.calls: list[LlmCallRecord] = []

    def record(self, call: LlmCallRecord) -> uuid.UUID:
        self.calls.append(call)
        return uuid.uuid4()

    def spent_since(self, user_id: uuid.UUID, since: datetime) -> int:
        return 0


@dataclass
class EvalResult:
    task: LlmTask
    prompt_version: str
    items: int
    answered: int = 0
    agree: Counter[str] = field(default_factory=Counter)  # per field, over the answered
    counted: Counter[str] = field(default_factory=Counter)
    disagreements: list[Disagreement] = field(default_factory=list)
    calls: list[LlmCallRecord] = field(default_factory=list)  # this run's only
    stopped: Stop | None = None

    @property
    def cost_micros(self) -> int:
        return sum(call.cost_micros for call in self.calls)

    def rate(self, name: str) -> float | None:
        return self.agree[name] / self.counted[name] if self.counted[name] else None


class Evaluator:
    """Runs golden sets through the gateway; `ledger` counts what the calls cost."""

    def __init__(self, gateway: LlmGateway, ledger: MemoryLedger, settings: TaskSettings) -> None:
        self._gateway, self._ledger, self._settings = gateway, ledger, settings

    def label_mentions(
        self, items: Sequence[GoldenItem], brand: GoldenBrand, *, cap_micros: int
    ) -> EvalResult:
        """Label the golden mentions in the product's batches, and compare with the expected;
        items a stopped run never sent count as unanswered."""
        result = EvalResult(LlmTask.LABEL_MENTIONS, PROMPTS[LlmTask.LABEL_MENTIONS], len(items))
        first, in_a_row = len(self._ledger.calls), 0
        for start in range(0, len(items), BATCH):
            batch = items[start : start + BATCH]
            result.calls = self._ledger.calls[first:]
            if result.stopped is None and _would_pass(result.calls, cap_micros):
                result.stopped = Stop.CAP
            answers: dict[str, Label] = {}
            if result.stopped is None:
                try:
                    answers, in_a_row = self._ask(brand, batch), 0
                except (LlmCallFailed, LlmOutputRejected) as exc:  # the ledger has the outcome
                    in_a_row += 1
                    hopeless = isinstance(exc, LlmCallFailed) and not exc.retryable
                    result.stopped = Stop.FAILURES if hopeless or in_a_row >= STOP_AFTER else None
            for item in batch:
                _compare(result, item, answers.get(item.id))
        result.calls = self._ledger.calls[first:]
        return result

    def _ask(self, brand: GoldenBrand, batch: Sequence[GoldenItem]) -> dict[str, Label]:
        """The model's labels by item id."""
        call = Call(
            task=LlmTask.LABEL_MENTIONS,
            prompt_version=PROMPTS[LlmTask.LABEL_MENTIONS],
            variables={
                "brand": brand.name,
                "aliases": brand.aliases,
                "not_the_brand": brand.not_the_brand,
                "mentions": [_record(item) for item in batch],
            },
            settings=self._settings,
            user_id=EVAL_USER,
        )
        answer = self._gateway.run(call, Labels)
        return {label.id: label for label in answer.output.labels}


def _would_pass(calls: Sequence[LlmCallRecord], cap_micros: int) -> bool:
    """Another call like the dearest so far would take the spend past the cap."""
    costs = [call.cost_micros for call in calls]
    return bool(costs) and sum(costs) + max(costs) > cap_micros


def _record(item: GoldenItem) -> dict[str, str]:
    record = {"id": item.id, "source": item.source, "text": item.text}
    if item.language:
        record["language"] = item.language
    return record


def _as_stored(got: Label) -> Label:
    """The values the product stores (`labelling._label`): a text the model calls unrelated
    gets fixed ones, whatever else it said."""
    if got.is_about_brand:
        return got
    fixed = {"sentiment": 0, "severity": 0, "topic": Topic.OTHER, "is_complaint": False}
    return got.model_copy(update=fixed)


def _compare(result: EvalResult, item: GoldenItem, answer: Label | None) -> None:
    _check(result, item.id, "answered", (True, answer is not None))
    if answer is None:
        return
    result.answered += 1
    expected, got = item.expected, _as_stored(answer)
    _check(result, item.id, "is_about_brand", (expected.is_about_brand, got.is_about_brand))
    if not expected.is_about_brand:
        return
    sentiment = (expected.sentiment, got.sentiment)
    _tally(result, f"sentiment[{expected.sentiment}]", ok=sentiment[0] == sentiment[1])
    _check(result, item.id, "sentiment", sentiment)
    _check(result, item.id, "topic", (expected.topic, got.topic))
    _check(result, item.id, "is_complaint", (expected.is_complaint, got.is_complaint))
    close = abs(expected.severity - got.severity) <= SEVERITY_SLACK
    severity = (expected.severity, got.severity)
    _check(result, item.id, f"severity±{SEVERITY_SLACK}", severity, agreed=close)


def _check(
    result: EvalResult,
    item_id: str,
    name: str,
    pair: tuple[object, object],
    agreed: bool | None = None,
) -> None:
    """Count one field's agreement and list a disagreement."""
    expected, got = pair
    ok = expected == got if agreed is None else agreed
    _tally(result, name, ok=ok)
    if not ok:
        result.disagreements.append(Disagreement(item_id, name, expected, got))


def _tally(result: EvalResult, name: str, *, ok: bool) -> None:
    result.counted[name] += 1
    result.agree[name] += ok

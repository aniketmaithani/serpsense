"""Score the explanation and drafting prompts against their golden sets (AGENTS §7, ADR-0008).

Each golden case is the brief or story a service would send, with what a good answer must and
must not do; the case goes through the real gateway with an in-memory ledger (nothing is stored),
one call per case, built by the services' own variable functions, and the answer is scored by
the deterministic checks in `eval_checks`. A spend cap stops the run before a call that would
likely pass it, and two failed calls in a row, or one no retry fixes, stop it too.
"""

import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, TypeVar

from pydantic import BaseModel

from serpsense.domain.enums import (
    AlertRule,
    CrisisComponent,
    CrisisLevel,
    DraftKind,
    LlmTask,
    MentionSource,
)
from serpsense.domain.llm_capabilities import TaskSettings
from serpsense.ports.drafts import DraftMaterial, SourceMention
from serpsense.ports.explanations import AlertBrief, BriefMention
from serpsense.ports.llm_client import LlmCallFailed, Variables
from serpsense.ports.llm_ledger import LlmCallRecord
from serpsense.services import drafts, explanations
from serpsense.services.eval_checks import (
    DraftExpected,
    ExplanationExpected,
    draft_checks,
    explanation_checks,
    invented_numbers,
    numbers,
)
from serpsense.services.evals import EVAL_USER, MemoryLedger, Split, Stop
from serpsense.services.labelling import STOP_AFTER
from serpsense.services.llm_gateway import Call, LlmGateway, LlmOutputRejected


class GoldenMention(BaseModel):
    source: MentionSource
    sentiment: Literal[-1, 0, 1] | None = None
    text: str


class Case(BaseModel):
    id: str
    split: Split
    synthetic: bool = False  # written for the set, not taken from recorded data
    difficulty: str = "normal"
    notes: str = ""
    brand: str
    mentions: list[GoldenMention]
    forbidden: list[str] = []  # what a planted instruction in a mention asks for


class ExplainCase(Case):
    rule: AlertRule
    competitor: bool = False
    level: CrisisLevel | None
    previous_level: CrisisLevel | None = None
    crisis: int
    health: int | None = None
    components: dict[CrisisComponent, int]
    story_label: str | None = None
    story_summary: str | None = None
    level_terms: list[str]
    topic_terms: list[str]

    def brief(self) -> AlertBrief:
        return AlertBrief(
            alert_id=uuid.UUID(int=0), scan_id=uuid.UUID(int=0), owner_id=EVAL_USER,
            brand_name=self.brand, competitor=self.competitor, rule=self.rule, level=self.level,
            previous_level=self.previous_level, crisis=self.crisis, health=self.health,
            components=self.components, story_label=self.story_label,
            story_summary=self.story_summary, explained=False,
            mentions=tuple(BriefMention(source=m.source, text=m.text, sentiment=m.sentiment)
                           for m in self.mentions),
        )  # fmt: skip

    def group(self) -> str:
        return self.rule.value

    def expected(self) -> ExplanationExpected:
        facts = [str(self.crisis), str(self.health or ""), *map(str, self.components.values())]
        facts += [self.story_label or "", self.story_summary or ""]
        given = numbers([*facts, *(m.text for m in self.mentions)]) | {"100"}  # a score's scale
        if self.components.get(CrisisComponent.PRESS):
            given.add("48")  # press counts the last 48 hours: the fact behind the component
        if self.rule is AlertRule.NARRATIVE_SPREAD:
            given |= {"5", "2"}  # the rule that fired: 5 mentions on 2 kinds of result
        return ExplanationExpected(
            brand=self.brand, level_terms=tuple(self.level_terms),
            topic_terms=tuple(self.topic_terms), given_numbers=frozenset(given),
            story_label=self.story_label, forbidden=tuple(self.forbidden),
        )  # fmt: skip


class DraftCase(Case):
    kind: DraftKind
    story_label: str
    story_summary: str
    on_topic: list[str]  # short ids (m1, m2, …) a good draft cites at least one of
    off_topic: list[str] = []
    competitors: list[str] = ["Uber", "Rapido", "Namma Yatri", "inDrive", "Bharat Taxi"]

    def group(self) -> str:
        return self.kind.value

    def material(self) -> DraftMaterial:
        return DraftMaterial(
            owner_id=EVAL_USER, brand_name=self.brand, story_label=self.story_label,
            story_summary=self.story_summary,
            mentions=tuple(SourceMention(mention_id=uuid.UUID(int=n), source=m.source,
                                         text=m.text, url=None, sentiment=m.sentiment)
                           for n, m in enumerate(self.mentions, start=1)),
        )  # fmt: skip

    def expected(self) -> DraftExpected:
        shown = tuple(f"m{n}" for n in range(1, len(self.mentions) + 1))
        return DraftExpected(
            kind=self.kind, shown=shown, on_topic=tuple(self.on_topic),
            off_topic=tuple(self.off_topic), competitors=tuple(self.competitors),
            forbidden=tuple(self.forbidden),
        )  # fmt: skip


class Outcome(StrEnum):
    ANSWERED = "answered"
    UNANSWERED = "unanswered"


@dataclass(frozen=True)
class Answer:
    """What the model said for a case, kept so a baseline can be read back."""

    case_id: str
    group: str  # the alert's rule, or the draft's kind
    text: str
    cited: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scored:
    checks: Mapping[str, bool]
    details: Mapping[str, str]  # why a check failed: numbers or ids, never the text
    answer: Answer


@dataclass
class OutputResult:
    task: LlmTask
    prompt_version: str
    items: int
    passed: Counter[str] = field(default_factory=Counter)
    counted: Counter[str] = field(default_factory=Counter)
    failures: list[tuple[str, str, str]] = field(default_factory=list)  # (case, check, detail)
    calls: list[LlmCallRecord] = field(default_factory=list)  # this run's only
    stopped: Stop | None = None
    groups: dict[str, str] = field(default_factory=dict)  # case id: its rule or kind
    group_passed: Counter[str] = field(default_factory=Counter)  # checks, by rule or kind
    group_counted: Counter[str] = field(default_factory=Counter)
    answers: list[Answer] = field(default_factory=list)

    @property
    def cost_micros(self) -> int:
        return sum(call.cost_micros for call in self.calls)

    def tally(
        self, case_id: str, checks: Mapping[str, bool], details: Mapping[str, str] | None = None
    ) -> None:
        group = self.groups.get(case_id, "")
        for name, ok in checks.items():
            self.counted[name] += 1
            self.passed[name] += ok
            self.group_counted[group] += 1
            self.group_passed[group] += ok
            if not ok:
                self.failures.append((case_id, name, (details or {}).get(name, "")))


C = TypeVar("C", ExplainCase, DraftCase)
TIMEOUT = 90.0  # seconds a case's call may take


class OutputEvaluator:
    def __init__(
        self, gateway: LlmGateway, ledger: MemoryLedger, settings: Mapping[LlmTask, TaskSettings]
    ) -> None:
        self._gateway, self._ledger, self._settings = gateway, ledger, settings

    def explanations(self, cases: Sequence[ExplainCase], *, cap_micros: int) -> OutputResult:
        def ask(case: ExplainCase) -> Scored:
            call = self._call(LlmTask.EXPLAIN_CRISIS, explanations.variables(case.brief()))
            text = self._gateway.run(call, explanations.Explanation).output.explanation
            invented = ", ".join(sorted(invented_numbers(text, case.expected())))
            checks = explanation_checks(text, case.expected())
            said = Answer(case.id, case.group(), text)
            return Scored(checks, {"no_invented_numbers": invented}, said)

        return self._run(LlmTask.EXPLAIN_CRISIS, cases, ask, cap_micros)

    def drafts(self, cases: Sequence[DraftCase], *, cap_micros: int) -> OutputResult:
        def ask(case: DraftCase) -> Scored:
            variables = drafts.variables(case.material(), case.kind)
            call = self._call(LlmTask.DRAFT_RESPONSE, variables)
            answer = self._gateway.run(call, drafts.Drafted).output
            cited = ", ".join(answer.cited)
            checks = draft_checks(answer.text, answer.cited, case.expected())
            said = Answer(case.id, case.group(), answer.text, tuple(answer.cited))
            ids = {"cites_the_story": cited, "cites_nothing_off_the_story": cited}
            return Scored(checks, ids, said)

        return self._run(LlmTask.DRAFT_RESPONSE, cases, ask, cap_micros)

    def _call(self, task: LlmTask, variables: Variables) -> Call:
        version = PROMPTS[task]
        return Call(task=task, prompt_version=version, variables=variables,
                    settings=self._settings[task], user_id=EVAL_USER,
                    timeout_seconds=TIMEOUT)  # fmt: skip

    def _run(
        self,
        task: LlmTask,
        cases: Sequence[C],
        ask: Callable[[C], Scored],
        cap_micros: int,
    ) -> OutputResult:
        result = OutputResult(task, PROMPTS[task], len(cases))
        first, in_a_row = len(self._ledger.calls), 0
        for case in cases:
            result.groups[case.id] = case.group()
            result.calls = self._ledger.calls[first:]
            if result.stopped is None and _would_pass(result.calls, cap_micros):
                result.stopped = Stop.CAP
            if result.stopped is not None:
                result.tally(case.id, {Outcome.ANSWERED: False})
                continue
            try:
                scored, in_a_row = ask(case), 0
                result.tally(case.id, {Outcome.ANSWERED: True, **scored.checks}, scored.details)
                result.answers.append(scored.answer)
            except (LlmCallFailed, LlmOutputRejected) as exc:  # the ledger has the outcome
                in_a_row += 1
                result.tally(case.id, {Outcome.ANSWERED: False})
                hopeless = isinstance(exc, LlmCallFailed) and not exc.retryable
                result.stopped = Stop.FAILURES if hopeless or in_a_row >= STOP_AFTER else None
        result.calls = self._ledger.calls[first:]
        return result


PROMPTS = {
    LlmTask.EXPLAIN_CRISIS: explanations.PROMPT_VERSION,
    LlmTask.DRAFT_RESPONSE: drafts.PROMPT_VERSION,
}


def _would_pass(calls: Sequence[LlmCallRecord], cap_micros: int) -> bool:
    """Another call like the dearest so far would take the spend past the cap."""
    costs = [call.cost_micros for call in calls]
    return bool(costs) and sum(costs) + max(costs) > cap_micros

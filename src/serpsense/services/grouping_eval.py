"""Score narrative grouping against its golden set (AGENTS §7, ADR-0008).

The golden set gives each unfavourable mention the story it tells, or none, and the brand's open
narratives (`group_narratives.brand.json`). A run sends the items through the real gateway as
the grouper would, in its batches, each offered the same open narratives, with an in-memory
ledger and the spend cap of `services/evals.py`. Answers are read as the product reads them
(`grouping_answer.placements`); an item whose placement the product would set aside is counted
as dropped, not as a one-off. Agreement is pairwise: two mentions of one story should end up in
the same narrative (recall), and two mentions placed together should share a story (precision);
mentions of open narratives should join them, and one-offs should be left alone.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations

from pydantic import BaseModel

from serpsense.domain.enums import LlmCallOutcome, LlmTask, MentionSource, Topic
from serpsense.domain.llm_capabilities import TaskSettings
from serpsense.ports.llm_client import LlmCallFailed
from serpsense.ports.llm_ledger import LlmCallRecord
from serpsense.ports.narrative_store import OpenNarrative, UngroupedMention
from serpsense.services.evals import EVAL_USER, MemoryLedger, Split, Stop, would_pass
from serpsense.services.grouping import BATCH, PROMPT_VERSION
from serpsense.services.grouping_answer import (
    Grouping,
    mention_record,
    narrative_record,
    placements,
)
from serpsense.services.llm_gateway import Call, LlmGateway, LlmOutputRejected

UNLABELLED = datetime.min.replace(tzinfo=UTC)  # golden items carry no label time


class GoldenStory(BaseModel):
    """An open narrative the brand has when the run starts."""

    story: str
    label: str
    summary: str
    mentions: int


class GroupingBrand(BaseModel):
    """`group_narratives.brand.json`: the brand, as its README fixes it."""

    name: str
    aliases: str = ""
    open: list[GoldenStory] = []


class GoldenMention(BaseModel):
    """One item of `evals/golden/group_narratives.jsonl`."""

    id: str
    source: MentionSource
    language: str | None = None
    topic: Topic
    severity: int
    text: str
    story: str | None  # None: a one-off
    split: Split
    difficulty: str = "normal"


@dataclass
class GroupingResult:
    items: Sequence[GoldenMention]
    placed: dict[str, str | None] = field(default_factory=dict)  # by item: its group, or None
    dropped: list[str] = field(default_factory=list)  # items the product would set aside
    ignored: int = 0  # answers repeated or naming no item
    started: list[str] = field(default_factory=list)  # the new narratives' labels
    calls: list[LlmCallRecord] = field(default_factory=list)
    stopped: Stop | None = None

    @property
    def answered(self) -> int:
        return len(self.placed)

    @property
    def cost_micros(self) -> int:
        return sum(call.cost_micros for call in self.calls)

    def pairs(self) -> tuple[int, int, int]:
        """Pairs of answered items placed together in both, only by the model, only in gold."""
        both = model = gold = 0
        answered = [item for item in self.items if item.id in self.placed]
        for one, two in combinations(answered, 2):
            together = (
                self.placed[one.id] is not None and self.placed[one.id] == self.placed[two.id]
            )
            same = one.story is not None and one.story == two.story
            both, model, gold = both + (together and same), model + together, gold + same
        return both, model, gold

    def joined_open(self, stories: set[str]) -> tuple[int, int]:
        """Items of an open narrative's story placed in it, of those answered."""
        theirs = [i for i in self.items if i.story in stories and i.id in self.placed]
        return sum(self.placed[i.id] == i.story for i in theirs), len(theirs)

    def one_offs(self) -> tuple[int, int]:
        """Golden one-offs the model left in no narrative, of those answered."""
        theirs = [i for i in self.items if i.story is None and i.id in self.placed]
        return sum(self.placed[i.id] is None for i in theirs), len(theirs)


class GroupingEvaluator:
    """Runs the grouping golden set through the gateway; `ledger` counts what calls cost."""

    def __init__(self, gateway: LlmGateway, ledger: MemoryLedger, settings: TaskSettings) -> None:
        self._gateway, self._ledger, self.settings = gateway, ledger, settings

    def group_narratives(
        self, items: Sequence[GoldenMention], brand: GroupingBrand, *, cap_micros: int
    ) -> GroupingResult:
        result, first = GroupingResult(items), len(self._ledger.calls)
        for start in range(0, len(items), BATCH):
            result.calls = self._ledger.calls[first:]
            if would_pass(result.calls, cap_micros):
                result.stopped = Stop.CAP
                break
            try:
                self._ask(result, brand, items[start : start + BATCH])
            except (LlmCallFailed, LlmOutputRejected):  # the ledger has the outcome
                result.stopped = Stop.FAILURES  # a set this small is one or two batches
                break
        result.calls = self._ledger.calls[first:]
        return result

    def _ask(
        self, result: GroupingResult, brand: GroupingBrand, batch: Sequence[GoldenMention]
    ) -> None:
        known = {
            f"n{n}": OpenNarrative(uuid.uuid4(), story.label, story.summary, story.mentions)
            for n, story in enumerate(brand.open, start=1)
        }
        mentions = {
            f"m{n}": UngroupedMention(
                mention_id=uuid.uuid4(),
                source=item.source,
                language_code=item.language,
                text=item.text,
                topic=item.topic,
                severity=item.severity,
                labelled_at=UNLABELLED,
            )
            for n, item in enumerate(batch, start=1)
        }
        call = Call(
            task=LlmTask.GROUP_NARRATIVES,
            prompt_version=PROMPT_VERSION,
            variables={
                "brand": brand.name,
                "aliases": brand.aliases,
                "narratives": [narrative_record(key, story) for key, story in known.items()],
                "mentions": [mention_record(key, mention) for key, mention in mentions.items()],
            },
            settings=self.settings,
            user_id=EVAL_USER,
        )
        answer = self._gateway.run(call, Grouping)
        placed = placements(answer.output, EVAL_USER, known, mentions)
        groups = {k.narrative_id: s.story for k, s in zip(known.values(), brand.open, strict=True)}
        for narrative in placed.narratives:
            result.started.append(narrative.label)
            groups[narrative.narrative_id] = f"new{len(result.started)}"
        joined = {a.mention_id: groups[a.narrative_id] for a in placed.assignments}
        for key, item in zip(mentions, batch, strict=True):
            if key in placed.one_offs:
                result.placed[item.id] = None
            elif (group := joined.get(mentions[key].mention_id)) is not None:
                result.placed[item.id] = group
            else:
                result.dropped.append(item.id)
        result.ignored += placed.ignored


def grouping_report(result: GroupingResult, brand: GroupingBrand, *, on: str, model: str) -> str:
    """The markdown report for evals/reports/<date>-group_narratives.md."""
    both, by_model, in_gold = result.pairs()
    precision, recall = _rate(both, by_model), _rate(both, in_gold)
    joined, of = result.joined_open({story.story for story in brand.open})
    left, alone = result.one_offs()
    failed = sum(call.outcome is not LlmCallOutcome.SUCCEEDED for call in result.calls)
    latency = sum(call.latency_ms for call in result.calls) / max(len(result.calls), 1)
    stopped = ""
    if result.stopped:
        stopped = f"\n- **Stopped** ({result.stopped.value}): not every item was sent."
    rows = "\n".join(
        f"| {i.id} | {i.difficulty} | {i.story or '-'} | {_shown(result, i.id)} |"
        for i in result.items
    )
    numbered = enumerate(result.started, start=1)
    started = "\n".join(f"- new{n}: {label}" for n, label in numbered) or "- none"
    return f"""# Eval: group_narratives ({PROMPT_VERSION})

- Run on {on}, model `{model}`.
- Items: {len(result.items)}, answered: {result.answered}.
- Calls: {len(result.calls)} ({failed} without a usable answer); mean latency {latency:,.0f} ms.
- Cost: ${result.cost_micros / 1_000_000:.4f}.{stopped}

## Agreement with the golden stories

| Measure | Value |
|---|---|
| Pairs placed together by the model | {by_model} |
| Pairs of one story in the golden set | {in_gold} |
| Pairs in both | {both} |
| Precision | {_pct(precision)} |
| Recall | {_pct(recall)} |
| F1 | {_pct(f1(precision, recall))} |
| Joined the open narrative | {joined}/{of} |
| One-offs left alone | {left}/{alone} |
| Placements set aside (items dropped, answers ignored) | {len(result.dropped)}, {result.ignored} |

## Narratives the model started (AI-generated labels)

{started}

## Placements

`-` is a one-off; `dropped` is an item whose placement the product would set aside (it scores
no pairs). A golden story with one item may start a narrative alone; it scores no pairs.

| Item | Difficulty | Golden story | Model's narrative |
|---|---|---|---|
{rows}
"""


def f1(precision: float | None, recall: float | None) -> float | None:
    """Their harmonic mean; None when either is undefined, 0 when both are 0."""
    if precision is None or recall is None:
        return None
    total = precision + recall
    return 2 * precision * recall / total if total > 0 else 0.0


def _shown(result: GroupingResult, item_id: str) -> str:
    if item_id in result.dropped:
        return "dropped"
    return result.placed.get(item_id) or "-"


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _pct(rate: float | None) -> str:
    return "-" if rate is None else f"{rate:.1%}"

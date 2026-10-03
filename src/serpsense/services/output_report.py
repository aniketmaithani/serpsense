"""The markdown report of an explanation or draft eval run, for evals/reports/<date>-<task>.md
(AGENTS §7): each check's pass rate, the same by rule or kind, how much one case moves a rate at
this size, mean and p95 latency, every failed check with its detail, and the model's answers, so
the baseline can be read back. The answers are model output about public mentions; they carry no
secret and no personal data (author names never reach the model)."""

import math
from collections import Counter
from collections.abc import Sequence

from serpsense.services.evals import Stop
from serpsense.services.output_evals import Outcome, OutputResult

STOPPED = {Stop.CAP: "Stopped at the spend cap", Stop.FAILURES: "Stopped after failed calls"}


def p95(values: Sequence[int]) -> int:
    """The 95th percentile by nearest rank; 0 for no values."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def output_report(result: OutputResult, *, on: str, model: str, split: str) -> str:
    answered = result.passed[Outcome.ANSWERED]
    latencies = [call.latency_ms for call in result.calls]
    mean = sum(latencies) / max(len(latencies), 1)
    per_case = result.cost_micros / max(answered, 1) / 1_000_000
    why = STOPPED.get(result.stopped) if result.stopped else None
    stopped = f"\n\n**{why}**: not every case was sent." if why else ""
    return f"""# Eval: {result.task.value} ({result.prompt_version})

- Run on {on}, model `{model}`, golden split `{split}`.
- Cases: {result.items}, answered: {answered}; one call each, {len(result.calls)} in all.
- Cost: ${result.cost_micros / 1_000_000:.4f} in all, ${per_case:.4f} per answered case.
- Latency: mean {mean:,.0f} ms, p95 {p95(latencies):,} ms.{stopped}

## Checks

Deterministic checks of each answer (`services/eval_checks.py`); no model judges another.
{_weight(result)}

| Check | Passed | Rate |
|---|---|---|
{_checks(result)}

## By {"rule" if result.task.value == "explain_crisis" else "kind"}

| Group | Cases | Checks passed | Rate |
|---|---|---|---|
{_groups(result)}

## Failed checks

The detail is the unexpected numbers, or the ids a draft cited.

| Case | Check | Detail |
|---|---|---|
{_failures(result)}

## Answers (AI-generated)

{_answers(result)}
"""


def _weight(result: OutputResult) -> str:
    if not result.items:
        return ""
    points = 100 / result.items
    return (
        f"With {result.items} cases, one case moves a check's rate by {points:.1f} points (more "
        "within a rule or kind), so a gate of 2 points can't be read at this size: compare "
        "failures case by case."
    )


def _checks(result: OutputResult) -> str:
    return "\n".join(
        f"| {name} | {result.passed[name]}/{result.counted[name]} | "
        f"{result.passed[name] / result.counted[name]:.1%} |"
        for name in result.counted
    )


def _groups(result: OutputResult) -> str:
    cases = Counter(result.groups.values())
    rows = [
        f"| {group} | {cases[group]} | {result.group_passed[group]}/"
        f"{result.group_counted[group]} | "
        f"{result.group_passed[group] / max(result.group_counted[group], 1):.1%} |"
        for group in sorted(cases)
    ]
    return "\n".join(rows) or "| - | - | - | - |"


def _failures(result: OutputResult) -> str:
    rows = [f"| {case} | {check} | {detail} |" for case, check, detail in result.failures]
    return "\n".join(rows) or "| - | - | - |"


def _answers(result: OutputResult) -> str:
    blocks = []
    for answer in result.answers:
        quoted = "\n".join(f"> {line}" if line else ">" for line in answer.text.splitlines())
        cited = f"\n\nCited: {', '.join(answer.cited)}" if answer.cited else ""
        blocks.append(f"### {answer.case_id} ({answer.group})\n\n{quoted}{cited}")
    return "\n\n".join(blocks) or "None."

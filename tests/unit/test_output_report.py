"""The explanation and draft eval report: rates, groups, how much a case weighs, latency, the
failed checks with their detail, why a run stopped, and the answers to read back."""

import json

import pytest

from serpsense.domain.llm_capabilities import OPUS
from serpsense.ports.llm_client import LlmCallFailed
from serpsense.services.output_report import output_report, p95
from tests.unit.test_output_evals import DRAFT, EXPLAIN, SAID, evaluator

pytestmark = pytest.mark.unit


def test_the_report_gives_p95_groups_and_the_answers() -> None:
    run, _ = evaluator(lambda request: json.dumps({"explanation": SAID}))
    result = run.explanations([EXPLAIN], cap_micros=10**9)
    text = output_report(result, on="2026-10-04", model=OPUS, split="test")
    assert "p95 900 ms" in text and "## By rule" in text and "| level_increase | 1 |" in text
    assert f"### e1 (level_increase)\n\n> {SAID}" in text
    assert "one case moves a check's rate by 100.0 points" in text
    assert p95([]) == 0 and p95(list(range(1, 21))) == 19 and p95([5]) == 5


def test_failed_checks_and_a_stopped_run_are_reported() -> None:
    said = SAID + " Up 37% this week."
    run, _ = evaluator(lambda request: json.dumps({"explanation": said}))
    text = output_report(run.explanations([EXPLAIN], cap_micros=10**9), on="x", model=OPUS,
                         split="test")  # fmt: skip
    assert "| e1 | no_invented_numbers | 37 |" in text
    timeout = LlmCallFailed("llm.timeout", retryable=True, latency_ms=5)
    failing, _ = evaluator(lambda request: timeout)
    result = failing.drafts([DRAFT] * 3, cap_micros=10**9)
    text = output_report(result, on="2026-10-04", model=OPUS, split="test")
    assert "**Stopped after failed calls**" in text and "| answered | 0/3 | 0.0% |" in text
    assert "## By kind" in text and "| faq_entry | 1 | 0/3 | 0.0% |" in text  # one id, thrice

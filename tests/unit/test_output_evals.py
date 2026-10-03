"""The explanation and drafting evals: golden cases become the services' own calls, answers are
checked, and runs stop at the cap or after failures."""

import json
from collections.abc import Callable

import pytest

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.services.evals import MemoryLedger, Stop
from serpsense.services.llm_gateway import LlmGateway
from serpsense.services.output_evals import DraftCase, ExplainCase, OutputEvaluator
from tests.fakes import FixedClock, ScriptedLlm
from tests.unit.test_scans import NOW

pytestmark = pytest.mark.unit

SETTINGS = {
    LlmTask.EXPLAIN_CRISIS: TaskSettings(OPUS, Effort.MEDIUM, 4000),
    LlmTask.DRAFT_RESPONSE: TaskSettings(OPUS, Effort.HIGH, 16000),
}
MENTIONS = [{"source": "play_review", "sentiment": -1, "text": "Driver asked 200 extra"}]
EXPLAIN = ExplainCase.model_validate(
    {"id": "e1", "split": "test", "brand": "Ola", "mentions": MENTIONS, "rule": "level_increase",
     "level": "medium", "previous_level": "low", "crisis": 46, "health": 58,
     "components": {"velocity": 70}, "level_terms": ["medium"], "topic_terms": ["extra"]}
)  # fmt: skip
DRAFT = DraftCase.model_validate(
    {"id": "d1", "split": "test", "brand": "Ola", "mentions": MENTIONS, "kind": "faq_entry",
     "story_label": "Extra cash", "story_summary": "Drivers ask for more.", "on_topic": ["m1"]}
)  # fmt: skip
SAID = "Ola's crisis level rose from low to medium, driven by reviews of drivers asking extra."
FAQ = "Why was I asked for extra cash?\n" + " ".join(["You only pay the fare in the app."] * 12)


def evaluator(
    answer: Callable[[LlmRequest], str | LlmCallFailed],
) -> tuple[OutputEvaluator, ScriptedLlm]:
    llm, ledger = ScriptedLlm(answer), MemoryLedger()
    gateway = LlmGateway(llm, ledger, FixedClock(NOW), monthly_budget_micros=lambda _: 2**62)
    return OutputEvaluator(gateway, ledger, SETTINGS), llm


def test_only_numbers_the_facts_supply_are_allowed_and_others_are_named() -> None:
    said = SAID + " Up 37% in 48 hours, a crisis score of 46 out of 100."
    run, _ = evaluator(lambda request: json.dumps({"explanation": said}))
    result = run.explanations([EXPLAIN], cap_micros=10**9)
    assert result.failures == [("e1", "no_invented_numbers", "37, 48")]  # no press component
    press = ExplainCase.model_validate({**EXPLAIN.model_dump(), "components": {"press": 40}})
    assert "48" in press.expected().given_numbers  # press counts the last 48 hours
    story = {"rule": "narrative_spread", "story_label": "Cash"}
    spread = ExplainCase.model_validate({**EXPLAIN.model_dump(), **story})
    assert {"5", "2"} <= spread.expected().given_numbers
    assert spread.expected().story_label == "Cash"


def test_an_explanation_case_is_sent_as_the_explainer_would_and_checked() -> None:
    run, llm = evaluator(lambda request: json.dumps({"explanation": SAID}))
    result = run.explanations([EXPLAIN], cap_micros=10**9)
    assert result.failures == [] and result.passed["answered"] == 1
    (request,) = llm.requests
    assert (request.prompt_version, request.variables["level"]) == ("explain_crisis/v1", "medium")
    assert request.shape.effort is Effort.MEDIUM


def test_a_draft_case_is_sent_as_the_drafter_would_and_checked() -> None:
    run, llm = evaluator(lambda request: json.dumps({"text": FAQ, "cited": ["m9"]}))
    result = run.drafts([DRAFT], cap_micros=10**9)
    assert set(result.failures) == {
        ("d1", "cites_only_what_it_was_shown", ""),
        ("d1", "cites_the_story", "m9"),  # the detail is the ids, never the text
    }
    (request,) = llm.requests
    assert request.variables["kind"] == "faq_entry" and request.shape.effort is Effort.HIGH


def test_failures_and_the_cap_stop_the_run() -> None:
    timeout = LlmCallFailed("llm.timeout", retryable=True, latency_ms=5)
    run, llm = evaluator(lambda request: timeout)
    result = run.drafts([DRAFT] * 3, cap_micros=10**9)
    assert result.stopped is Stop.FAILURES and len(llm.requests) == 2
    assert result.counted["answered"] == 3 and result.passed["answered"] == 0
    capped, llm = evaluator(lambda request: json.dumps({"explanation": SAID}))
    assert capped.explanations([EXPLAIN] * 3, cap_micros=1).stopped is Stop.CAP
    assert len(llm.requests) == 1

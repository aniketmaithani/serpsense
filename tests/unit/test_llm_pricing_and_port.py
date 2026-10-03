"""Model-call cost in integer micros, and the LLM port's request rules (ADR-0008)."""

import pytest

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import HAIKU, OPUS, SONNET, Effort, RequestShape
from serpsense.domain.llm_pricing import Hop, TokenUsage, UnknownModel, cost_micros, total
from serpsense.ports.llm_client import LlmCallFailed, LLMClient, LlmRequest, LlmResponse

pytestmark = pytest.mark.unit

SHAPE = RequestShape(OPUS, 4096, Effort.LOW, "adaptive", None)


@pytest.mark.parametrize(
    ("model", "usage", "micros"),
    [
        # $4 in / $20 out per MTok: 5 200 in + 900 out = 20 800 + 18 000 micros.
        (OPUS, TokenUsage(input=5200, output=900, cache_read=0, cache_write=0), 38_800),
        # Cache reads at $0.20, writes at 1.25x input.
        (OPUS, TokenUsage(input=0, output=0, cache_read=4000, cache_write=1000), 800 + 5000),
        (SONNET, TokenUsage(input=1000, output=1000, cache_read=0, cache_write=0), 12_000),
        (HAIKU, TokenUsage(input=1000, output=1000, cache_read=1000, cache_write=1000), 7_350),
        (OPUS, TokenUsage(input=1, output=0, cache_read=0, cache_write=0), 4),
        (OPUS, TokenUsage(input=0, output=0, cache_read=1, cache_write=0), 1),  # 0.2 rounds up
        (OPUS, TokenUsage(input=0, output=0, cache_read=0, cache_write=0), 0),
        # The models a refusal fallback serves, and Haiku's dated id.
        (
            "claude-opus-4-8",
            TokenUsage(input=1000, output=1000, cache_read=0, cache_write=0),
            30_000,
        ),
        ("claude-opus-5", TokenUsage(input=0, output=0, cache_read=1000, cache_write=0), 500),
        ("claude-sonnet-5", TokenUsage(input=1000, output=0, cache_read=0, cache_write=0), 2000),
        (
            "claude-haiku-4-5-20251001",
            TokenUsage(input=1000, output=0, cache_read=0, cache_write=0),
            1000,
        ),
    ],
)
def test_cost_is_integer_micros_rounded_up(model: str, usage: TokenUsage, micros: int) -> None:
    assert cost_micros(model, usage) == micros


def test_an_unpriced_model_can_t_be_accounted_for() -> None:
    with pytest.raises(UnknownModel):
        cost_micros("claude-3-opus", TokenUsage(input=1, output=1, cache_read=0, cache_write=0))
    with pytest.raises(ValueError, match="negative"):
        TokenUsage(input=-1, output=0, cache_read=0, cache_write=0)


def test_a_request_names_a_prompt_of_its_own_task() -> None:
    request = LlmRequest(LlmTask.LABEL_MENTIONS, "label_mentions/v1", {}, SHAPE, {})
    assert request.prompt_caching and request.refusal_fallback
    with pytest.raises(ValueError, match="task"):
        LlmRequest(LlmTask.LABEL_MENTIONS, "draft_response/v1", {}, SHAPE, {})


def test_a_failed_call_carries_a_code_never_provider_text() -> None:
    failed = LlmCallFailed("llm.timeout", retryable=True, latency_ms=60_000)
    assert (str(failed), failed.retryable, failed.latency_ms) == ("llm.timeout", True, 60_000)


class EchoClient:
    def complete(self, request: LlmRequest) -> LlmResponse:
        usage = TokenUsage(input=1, output=1, cache_read=0, cache_write=0)
        hops = (Hop(request.shape.model, usage),)
        return LlmResponse(request.shape.model, "end_turn", "{}", hops, latency_ms=1)


def test_a_client_answers_with_the_served_model_and_usage() -> None:
    client: LLMClient = EchoClient()
    request = LlmRequest(LlmTask.LABEL_MENTIONS, "label_mentions/v1", {}, SHAPE, {})
    response = client.complete(request)
    assert (response.served_model, response.output, response.reasoning_summary) == (
        OPUS,
        "{}",
        None,
    )


def test_a_calls_usage_adds_up_every_hop_and_repr_shows_no_content() -> None:
    declined = Hop(OPUS, TokenUsage(input=1000, output=400, cache_read=0, cache_write=0))
    answered = Hop(
        "claude-opus-4-8", TokenUsage(input=1000, output=600, cache_read=0, cache_write=0)
    )
    response = LlmResponse("claude-opus-4-8", "end_turn", '{"secret": 1}', (declined, answered), 9)
    assert response.usage == total([declined, answered]) == TokenUsage(2000, 1000, 0, 0)
    request = LlmRequest(
        LlmTask.LABEL_MENTIONS, "label_mentions/v1", {"brand": "secret brand"}, SHAPE, {}
    )
    assert "secret" not in repr(response) and "secret" not in repr(request)

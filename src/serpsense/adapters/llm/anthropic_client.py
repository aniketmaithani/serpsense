"""The LLMClient on the Anthropic SDK, the only place it is imported (AGENTS §2, ADR-0008).

It renders the versioned prompt, translates the checked RequestShape into SDK parameters,
asks for structured output, and maps the response and the SDK's errors back to the port. With
refusal fallback on, Opus and Sonnet calls go through the beta endpoint with `fallbacks:
"default"`, so a declined request is re-run on the recommended model and `response.model` names
the one that answered; every model that ran is billed, so the usage comes per hop from
`usage.iterations`. The API host is pinned (ADR-0011), so the SDK's environment overrides can't
redirect a call. Prompts and completions are never logged.
"""

import time
from collections.abc import Callable
from typing import Any, Protocol

import anthropic
from anthropic.types import Message
from anthropic.types.beta import BetaMessage

from serpsense.adapters.llm.prompts import PromptLibrary
from serpsense.domain.llm_capabilities import OPUS, SONNET, RequestShape
from serpsense.domain.llm_pricing import Hop, TokenUsage
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse

API_HOST = "https://api.anthropic.com"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset({OPUS, SONNET})
SDK_RETRIES = 2


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        prompts: PromptLibrary,
        *,
        sdk: anthropic.Anthropic | None = None,  # a client on a mock transport in tests
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sdk = sdk or anthropic.Anthropic(
            api_key=api_key, base_url=API_HOST, max_retries=SDK_RETRIES
        )
        self._prompts = prompts
        self._timer = timer

    def complete(self, request: LlmRequest) -> LlmResponse:
        prompt = self._prompts.render(request.prompt_version, request.variables)
        params = _params(request, prompt.system, prompt.user)
        sdk = self._sdk
        if request.max_retries is not None:  # a person waiting on one call: no silent retries
            sdk = sdk.with_options(max_retries=request.max_retries)
        started = self._timer()
        try:
            if request.refusal_fallback and request.shape.model in FALLBACK_MODELS:
                params.update(betas=[FALLBACK_BETA], fallbacks="default")
                message = sdk.beta.messages.create(**params)
            else:
                message = sdk.messages.create(**params)
        except anthropic.APIError as exc:
            raise _failure(exc, _elapsed(started, self._timer)) from exc
        latency = _elapsed(started, self._timer)
        return _response(message, latency, request)


def _params(request: LlmRequest, system: str, user: str) -> dict[str, Any]:
    shape = request.shape
    output: dict[str, Any] = {
        # Structured outputs take a subset of JSON Schema: the SDK's transform moves limits
        # such as maximum and maxLength into descriptions and closes every object. The gateway
        # still validates the answer against the full model.
        "format": {
            "type": "json_schema",
            "schema": anthropic.transform_schema(dict(request.output_schema)),
        }
    }
    if shape.effort is not None:
        output["effort"] = shape.effort.value  # Opus's default is medium: always sent
    block: dict[str, Any] = {"type": "text", "text": system}
    if request.prompt_caching:
        block["cache_control"] = {"type": "ephemeral"}  # the stable prefix
    params: dict[str, Any] = {
        "model": shape.model,
        "max_tokens": shape.max_tokens,
        "system": [block],
        "messages": [{"role": "user", "content": user}],
        "output_config": output,
        "timeout": request.timeout_seconds,
    }
    thinking = _thinking(shape, request.reasoning_summary)
    if thinking is not None:
        params["thinking"] = thinking
    if shape.temperature is not None:
        # SDK 1.x dropped the keyword; Haiku 4.5 still honours it in the request body.
        params["extra_body"] = {"temperature": shape.temperature}
    return params


def _thinking(shape: RequestShape, summary: bool) -> dict[str, Any] | None:
    display = {"display": "summarized"} if summary else {}
    if shape.thinking == "adaptive":
        return {"type": "adaptive", **display}
    if shape.thinking == "between_tools":
        return {"type": "between_tools"}
    if isinstance(shape.thinking, int):
        return {"type": "enabled", "budget_tokens": shape.thinking, **display}
    return None  # Haiku with thinking off: no thinking parameter at all


def _response(message: Message | BetaMessage, latency_ms: int, request: LlmRequest) -> LlmResponse:
    want_summary = request.reasoning_summary
    texts = [block.text for block in message.content if block.type == "text"]
    thoughts = [block.thinking for block in message.content if block.type == "thinking"]
    refused = message.stop_reason == "refusal"
    return LlmResponse(
        served_model=message.model,
        stop_reason=message.stop_reason or "unknown",
        output=None if refused or not texts else texts[0],
        hops=_hops(message, request.shape.model),
        latency_ms=latency_ms,
        reasoning_summary=("\n".join(thoughts) or None) if want_summary else None,
    )


def _hops(message: Message | BetaMessage, requested: str) -> tuple[Hop, ...]:
    """Each model that ran: `usage.iterations` is the per-attempt record when a fallback
    may have run (a first attempt that names no model is the requested one); otherwise, or
    when it holds no message attempts, the top-level usage is the one attempt."""
    if isinstance(message, BetaMessage) and message.usage.iterations:
        hops = tuple(
            Hop(iteration.model or requested, _tokens(iteration))
            for iteration in message.usage.iterations
            if iteration.type in ("message", "fallback_message")
        )
        if hops:
            return hops
    return (Hop(message.model, _tokens(message.usage)),)


class _Counts(Protocol):
    """The usage of a message or of one iteration: the same four counts."""

    @property
    def input_tokens(self) -> int: ...
    @property
    def output_tokens(self) -> int: ...
    @property
    def cache_read_input_tokens(self) -> int | None: ...
    @property
    def cache_creation_input_tokens(self) -> int | None: ...


def _tokens(usage: _Counts) -> TokenUsage:
    return TokenUsage(
        input=usage.input_tokens,
        output=usage.output_tokens,
        cache_read=usage.cache_read_input_tokens or 0,
        cache_write=usage.cache_creation_input_tokens or 0,
    )


def _failure(exc: anthropic.APIError, latency_ms: int) -> LlmCallFailed:
    """Classify by type and status only; the SDK's message is never passed on."""
    if isinstance(exc, anthropic.APITimeoutError):
        return LlmCallFailed("llm.timeout", retryable=True, latency_ms=latency_ms)
    if isinstance(exc, anthropic.APIConnectionError):
        return LlmCallFailed("llm.network", retryable=True, latency_ms=latency_ms)
    status = exc.status_code if isinstance(exc, anthropic.APIStatusError) else 0
    if status == 429:
        return LlmCallFailed("llm.http_429", retryable=True, latency_ms=latency_ms)
    if status >= 500:
        return LlmCallFailed("llm.http_5xx", retryable=True, latency_ms=latency_ms)
    return LlmCallFailed("llm.http_4xx", retryable=False, latency_ms=latency_ms)


def _elapsed(started: float, timer: Callable[[], float]) -> int:
    return max(0, round((timer() - started) * 1000))

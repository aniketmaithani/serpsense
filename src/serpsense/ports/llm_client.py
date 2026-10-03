"""Port for model calls (ADR-0008, AGENTS §7).

Prompts are versioned files the adapter renders: a request names its task, prompt version and
variables, never prompt text, so no business code assembles a prompt. Mention text arrives as
variables and the adapter wraps it in delimiters as data. The adapter returns the raw structured
output and the usage; the gateway validates the output, prices the call and records it. Nothing
here is ever logged: prompts and completions are referred to by prompt version and call id.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import RequestShape
from serpsense.domain.llm_pricing import Hop, TokenUsage, total

Variables = Mapping[str, str | Sequence[Mapping[str, str]]]


@dataclass(frozen=True)
class LlmRequest:
    task: LlmTask
    prompt_version: str  # "<task>/v<N>": the prompt file the adapter renders
    variables: Variables = field(repr=False)  # untrusted text, never in the prompt file
    shape: RequestShape  # checked settings (domain.llm_capabilities.request_shape)
    output_schema: Mapping[str, Any]  # JSON schema of the structured output
    prompt_caching: bool = True  # cache the stable prefix (system prompt, taxonomy, examples)
    refusal_fallback: bool = True  # server-side fallback on a refusal (Opus and Sonnet)
    timeout_seconds: float = 60.0
    reasoning_summary: bool = False  # ask for a summary of the model's thinking
    max_retries: int | None = None  # the client's own retries; None keeps its default

    def __post_init__(self) -> None:
        if self.prompt_version.split("/")[0] != self.task.value:
            raise ValueError("a prompt version belongs to its task")


@dataclass(frozen=True)
class LlmResponse:
    served_model: str  # the model that answered; a refusal fallback can change it
    stop_reason: str
    output: str | None = field(repr=False)  # the structured output's JSON; None on a refusal
    hops: tuple[Hop, ...]  # each model that ran, with its usage: declined ones are billed too
    latency_ms: int
    reasoning_summary: str | None = field(default=None, repr=False)

    @property
    def usage(self) -> TokenUsage:
        return total(self.hops)


class LlmCallFailed(Exception):
    """No usable response: a network error, a timeout or an API error. The message is a code
    such as `llm.timeout`, never provider text, so it can't leak a prompt."""

    def __init__(self, code: str, *, retryable: bool, latency_ms: int) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.latency_ms = latency_ms


class PromptUnavailable(LookupError):
    """A prompt version with no file, or a placeholder with no variable: a deploy error, not
    the model's, which callers may contain like any other failed call."""


class LLMClient(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse:
        """One model call; raises LlmCallFailed when no response came back."""
        ...

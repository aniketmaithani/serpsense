"""The model when no Anthropic key is configured (replay mode, or a web container without one):
every call fails at once without touching the network, so a page can say the model is
unavailable instead of failing (ADR-0008)."""

from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse


class UnavailableClient:
    def complete(self, request: LlmRequest) -> LlmResponse:
        raise LlmCallFailed("llm.unconfigured", retryable=False, latency_ms=0)

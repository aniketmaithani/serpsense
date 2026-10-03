"""Port for the LLM call ledger, `llm_calls` (data-model §6, ADR-0008).

Every write runs in its own transaction, so a billed call is recorded even if what follows it
fails, and the model output that cites it can rely on the row being there. Rows hold ids,
counts, cost and settings, never a prompt or a completion.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from serpsense.domain.enums import LlmCallOutcome, LlmTask
from serpsense.domain.llm_pricing import TokenUsage


@dataclass(frozen=True)
class LlmCallRecord:
    user_id: uuid.UUID
    scan_id: uuid.UUID | None  # None for calls outside a scan, such as a draft
    task: LlmTask
    requested_model: str
    served_model: str | None  # None exactly when the call failed without a response
    prompt_version: str
    request_settings: Mapping[str, Any]  # the resolved settings, JSON-serialisable
    usage: TokenUsage
    cost_micros: int
    currency: str
    stop_reason: str | None
    outcome: LlmCallOutcome
    latency_ms: int
    created_at: datetime

    def __post_init__(self) -> None:
        """The ledger's own rules, checked before the insert would reject them."""
        if (self.outcome is LlmCallOutcome.FAILED) != (self.served_model is None):
            raise ValueError("a served model exactly when a response arrived")
        if self.prompt_version.split("/")[0] != self.task.value:
            raise ValueError("a prompt version belongs to its task")
        if self.cost_micros < 0 or self.latency_ms < 0:
            raise ValueError("cost and latency can't be negative")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


class LlmLedger(Protocol):
    def record(self, call: LlmCallRecord) -> uuid.UUID:
        """Insert one `llm_calls` row in a transaction of its own; returns its id."""
        ...

    def spent_since(self, user_id: uuid.UUID, since: datetime) -> int:
        """The user's model spend since a time, in micros, for the monthly budget."""
        ...

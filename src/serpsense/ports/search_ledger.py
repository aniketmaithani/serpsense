"""Port for the SerpApi ledger: `serp_calls` and `raw_responses` (data-model §5).

Every write runs in its own transaction, so a call is recorded even if what follows it fails,
and nothing a scan rolls back can erase a billed call.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from serpsense.domain.enums import SerpCallOutcome, SerpEngine, SerpErrorCode, ServedFrom
from serpsense.ports.search_provider import SearchRequest


@dataclass(frozen=True)
class CallRecord:
    """One attempt, or one call skipped before it was made."""

    user_id: uuid.UUID
    scan_id: uuid.UUID | None  # None for Preview
    request: SearchRequest
    outcome: SerpCallOutcome
    served_from: ServedFrom | None
    http_status: int | None
    error_code: SerpErrorCode | None
    latency_ms: int
    created_at: datetime

    def __post_init__(self) -> None:
        """The ledger's own rules, checked before SerpApi bills a call the insert would reject."""
        succeeded = self.outcome is SerpCallOutcome.SUCCEEDED
        failed = self.outcome is SerpCallOutcome.FAILED
        if succeeded != (self.served_from is not None):
            raise ValueError("served_from is set exactly for successful calls")
        if failed != (self.error_code is not None):
            raise ValueError("error_code is set exactly for failed calls")
        skipped = not (succeeded or failed)
        if skipped and (self.http_status is not None or self.latency_ms != 0):
            raise ValueError("a skipped call has no HTTP status and no latency")
        if self.latency_ms < 0:
            raise ValueError("latency can't be negative")


class SearchUsage(Protocol):
    """What the ledger knows about how many searches have been made."""

    def live_calls(
        self, *, since: datetime, user_id: uuid.UUID | None = None, scan_id: uuid.UUID | None = None
    ) -> int:
        """Billable calls (served live) since a time, for everyone, one user or one scan."""
        ...

    def monthly_budget(self, user_id: uuid.UUID, *, at: datetime) -> int | None:
        """The user's search budget in force at a time, or None if none was ever set."""
        ...


class SearchLedger(SearchUsage, Protocol):
    def record_call(self, call: CallRecord) -> uuid.UUID:
        """Insert one `serp_calls` row; returns its id."""
        ...

    def store_payload(
        self, call_id: uuid.UUID, payload: Mapping[str, Any], *, at: datetime
    ) -> None:
        """Keep the redacted payload of a successful call."""
        ...

    def recent_attempts(
        self, engine: SerpEngine, *, since: datetime, limit: int
    ) -> Sequence[SerpErrorCode | None]:
        """Attempts that reached SerpApi, newest first: an error code if failed, else None."""
        ...

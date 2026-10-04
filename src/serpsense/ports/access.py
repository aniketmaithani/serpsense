"""Port for access requests (data-model §1, ADR-0014).

In invite mode an address that isn't invited, asking for a code, leaves a request; the operator
approves or rejects it, each decision a new row, and the latest one counts. Addresses are
personal data: they live only in `access_requests`, which account deletion pseudonymises, and
are never logged.
"""

import uuid
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import AccessDecision


class AccessRequests(Protocol):
    def record(self, email: str, *, at: datetime) -> None:
        """Remember that the address asked for a code while not invited; asking again (in any
        case) changes nothing."""
        ...

    def recorded_between(self, start: datetime, end: datetime) -> int:
        """How many requests were first made in [start, end], across every address: what the
        hourly cap on new requests counts (ADR-0014)."""
        ...

    def approved(self, email: str) -> bool:
        """Whether the address (ignoring case) has a request whose latest decision approves it."""
        ...

    def decide(self, request_id: uuid.UUID, decision: AccessDecision, *, at: datetime) -> bool:
        """Record the operator's decision on a request (one at the same instant again changes
        nothing); False if there is no such request."""
        ...

"""Port for who may sign up: access requests and the sign-up mode (data-model §1, ADR-0014,
ADR-0015).

In invite mode an address that isn't invited, asking for a code, leaves a request; the operator
approves or rejects it, each decision a new row, and the latest one counts. Addresses are
personal data: they live only in `access_requests`, which account deletion pseudonymises, and
are never logged. The operator can also switch the sign-up mode; each switch is a new row, and the
latest one counts.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import AccessDecision, SignupMode


@dataclass(frozen=True)
class ModeSwitch:
    mode: SignupMode
    at: datetime


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

    def admit(self, email: str, *, at: datetime) -> None:
        """Approve the address as it signs up while sign-up is open (a request and an `approved`
        decision, unless it is approved already), so it stays in once sign-up closes
        (ADR-0015)."""
        ...

    def decide(self, request_id: uuid.UUID, decision: AccessDecision, *, at: datetime) -> bool:
        """Record the operator's decision on a request (one at the same instant again changes
        nothing); False if there is no such request."""
        ...

    def signup_mode(self) -> ModeSwitch | None:
        """The operator's latest switch of the sign-up mode; None if they never switched it (the
        environment's SIGNUP_MODE applies)."""
        ...

    def switch_signup_mode(self, mode: SignupMode, *, at: datetime) -> None:
        """Record a switch; another at the same instant changes nothing, so the first of a
        double click stands."""
        ...

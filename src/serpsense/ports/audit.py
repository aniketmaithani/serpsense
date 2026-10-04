"""Port for the audit log (ADR-0009, data-model §9): who did what, and from where.

Details never hold an email, IP, code or token; the request's IP and user agent go to a separate
row that account deletion scrubs (ADR-0013). A log works inside the caller's unit of work.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Protocol

from serpsense.domain.enums import AuditAction, AuditTarget


@dataclass(frozen=True)
class Network:
    ip: IPv4Address | IPv6Address | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class AuditEntry:
    action: AuditAction
    at: datetime
    actor_user_id: uuid.UUID | None = None
    target: tuple[AuditTarget, uuid.UUID] | None = None
    details: Mapping[str, str | int | bool] | None = None
    network: Network | None = None


class AuditLog(Protocol):
    def record(self, entry: AuditEntry) -> uuid.UUID: ...

    def operator_events_since(self, action: AuditAction, since: datetime) -> int:
        """How many events of this kind with no actor (the operator console's, ADR-0014)
        happened since then."""
        ...

    def last_operator_event(self, action: AuditAction) -> datetime | None:
        """When the latest such event happened; None if none has."""
        ...

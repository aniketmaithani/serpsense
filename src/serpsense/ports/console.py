"""Port for what the operator console shows (ADR-0014): read-only, across every user.

The one read path that isn't scoped to a user (data-model conventions); it serves only the
console, which the operator reaches with `ADMIN_PASSWORD`. Addresses shown here are never logged.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import AccessDecision


@dataclass(frozen=True, kw_only=True)
class Totals:
    users: int  # accounts not deleted
    brands: int  # not archived
    archived_brands: int
    brands_this_week: int  # added in the last 7 days
    scans_today: int  # created in the last 24 hours
    searches_this_month: int  # billable SerpApi searches since the month began (UTC)
    pending_requests: int


@dataclass(frozen=True, kw_only=True)
class RequestRow:
    request_id: uuid.UUID
    email: str
    requested_at: datetime
    decision: AccessDecision | None  # the latest; None while pending
    decided_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class BrandRow:
    name: str
    owner_email: str
    created_at: datetime
    archived: bool
    last_scan_at: datetime | None


class ConsoleReads(Protocol):
    def totals(self, at: datetime) -> Totals: ...

    def requests(self) -> list[RequestRow]:
        """Pending ones first, oldest first; then decided ones, latest decision first; at most
        200. Requests of deleted accounts (pseudonymised) are left out."""
        ...

    def brands(self) -> list[BrandRow]:
        """The newest brands first, at most 200, archived ones included."""
        ...

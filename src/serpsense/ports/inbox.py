"""Port for the in-app notifications centre (BUILD_PLAN §12, §13): one user's notifications,
newest first, and their read marks.

A notification is the user's own row; marking one read is an append-only fact, written once
(unread is the absence of a read mark). A notification about an alert links to the alert's brand
while that brand is still the user's and not archived.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class NotificationRow:
    notification_id: uuid.UUID
    title: str
    body: str
    at: datetime
    read: bool
    brand_id: uuid.UUID | None  # the alert's brand, to link to


class Inbox(Protocol):
    def latest(self, user_id: uuid.UUID, *, limit: int) -> list[NotificationRow]: ...

    def unread(self, user_id: uuid.UUID) -> int: ...

    def mark_read(
        self, user_id: uuid.UUID, notification_id: uuid.UUID, *, and_older: bool = False
    ) -> int:
        """Mark one of the user's notifications read, and with `and_older` every one before it
        too (so "mark all" covers what the page showed, not what arrived since); how many were
        newly marked. Another user's id marks nothing."""
        ...

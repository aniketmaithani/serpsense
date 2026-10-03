"""Port for nudging background work (ADR-0005).

Postgres holds every job's state and a message is only a nudge: a worker re-reads the state and
claims it with a compare-and-set. A nudge that never arrives is recovered by the sweep, so a
send that fails is logged rather than retried.
"""

import uuid
from typing import Protocol


class JobQueueUnavailable(Exception):
    """The broker didn't take the message; the sweep sends it again."""


class JobQueue(Protocol):
    def run_scan(self, scan_id: uuid.UUID) -> None:
        """Ask a worker to claim and run a queued scan."""
        ...

    def dispatch_outbox(self) -> None:
        """Ask the outbox worker to send due emails now rather than at its next Beat (ADR-0010:
        a sign-in code shouldn't wait)."""
        ...

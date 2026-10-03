"""The per-engine SerpApi circuit breaker, derived from the ledger (data-model §5, ADR-0007).

An engine is open when its last 5 attempts that reached SerpApi within 15 minutes all failed
transiently. Skipped calls and local-cache hits are not attempts, so an open breaker never
keeps itself open; it closes once those failures leave the window.
"""

from collections.abc import Sequence
from datetime import timedelta

from serpsense.domain.enums import SerpErrorCode

WINDOW = timedelta(minutes=15)
ATTEMPTS = 5


def is_open(recent_attempts: Sequence[SerpErrorCode | None]) -> bool:
    """`recent_attempts` are the newest attempts in the window, newest first: an error code for
    a failed attempt, None for a successful one."""
    latest = recent_attempts[:ATTEMPTS]
    return len(latest) == ATTEMPTS and all(
        code is not None and code.is_transient for code in latest
    )

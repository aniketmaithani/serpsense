"""The real clock is timezone-aware UTC (AGENTS §3)."""

from datetime import UTC, datetime, timedelta

import pytest

from serpsense.adapters.system_clock import SystemClock
from serpsense.ports.clock import Clock

pytestmark = pytest.mark.unit


def test_now_is_aware_utc_and_current() -> None:
    clock: Clock = SystemClock()  # mypy checks it satisfies the port
    now = clock.now()
    assert now.tzinfo is UTC
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)
